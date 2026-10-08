#!/usr/bin/env python3
"""Build cocktails-data.js from the bar's Compendium PDF.

    python3 tools/build_cocktail_data.py ~/Downloads/Compendium.pdf

Needs pypdf (pip3 install pypdf). Ingredient names are mapped to filter keys via
tools/ingredients.json. If a name can't be mapped, the build stops and lists it;
add an alias there and re-run. Fixes the PDF itself can't express (garnish lines
that are really credits, which landing card a drink belongs on, etc.) live in the
OVERRIDES section below.
"""
import json
import re
import sys
import unicodedata
from collections import Counter
from fractions import Fraction
from pathlib import Path

import pypdf

ROOT = Path(__file__).resolve().parent.parent
CATALOG = json.loads((ROOT / 'tools' / 'ingredients.json').read_text(encoding='utf-8'))
ING = {i['key']: i for i in CATALOG['ingredients']}


# ── 1. PDF → lines in reading order ──────────────────────────────────────────
# Plain extraction emits each page's list bullets after all of its headings, which
# scrambles which ingredients belong to which drink. Layout mode keeps page order.
def pdf_lines(path):
    for page in pypdf.PdfReader(path).pages:
        text = page.extract_text(extraction_mode='layout', layout_mode_space_vertically=False)
        for line in text.splitlines():
            line = re.sub(r'\s+', ' ', line.replace('﻿', '').replace('ʼ', '’')).strip()
            if line:
                yield line


# ── 2. Lines → tokens ────────────────────────────────────────────────────────
FIELD_RE = re.compile(r'^(Method|Glassware|Glass|Garnish|Origin|Ice|Variations)\s*:\s*(.*)$', re.I)

def tokenize(lines):
    for line in lines:
        is_bullet = line.startswith('•')
        text = line.lstrip('•').strip()
        m = FIELD_RE.match(text)
        if m:
            field = m.group(1).lower()
            yield ('field', 'glass' if field == 'glassware' else field, m.group(2).strip())
        elif is_bullet:
            if text:
                yield ('bullet', text)
        else:
            yield ('text', text)


# ── 3. Tokens → drinks ───────────────────────────────────────────────────────
# A run of plain-text lines followed by bullets is a drink header (title, then the
# heading repeated). Followed by anything else, it continues the previous field.
SUBSPEC_RE = re.compile(r'^(service recipe|single recipe|hh batch recipe|\*if guest calls.*)$', re.I)

def new_spec(label=None):
    return {'label': label, 'ingredients': [], 'fields': {}, 'notes': []}

def header_name(run):
    if run[0].startswith('*') and run[0].endswith('(category)'):
        return run[1] if len(run) > 1 else run[0][1:-len('(category)')].strip()
    # "Whiskey Sour" / "Whiskey Sour (egg white)" → the specific variant
    if len(run) > 1 and run[1] != run[0] and run[1].startswith(run[0]):
        return run[1]
    return run[0]

def title_like(s):
    return len(s) <= 40 and (s[0].isupper() or s[0].isdigit()) and not s.endswith(('.', ',', ';', ':'))

def split_header(run):
    """Split a text run into (continuation of the previous drink, this drink's 1–2 title lines)."""
    k = 2 if len(run) >= 2 and (run[-2].endswith('(category)') or title_like(run[-2])) else 1
    return run[:-k], run[-k:]

def parse(tokens):
    tokens = list(tokens)
    drinks, drink, spec, last_field = [], None, None, None
    i = 0
    while i < len(tokens):
        kind = tokens[i][0]
        if kind == 'text':
            run = []
            while i < len(tokens) and tokens[i][0] == 'text':
                run.append(tokens[i][1])
                i += 1
            if i < len(tokens) and tokens[i][0] == 'bullet':
                marker = run.pop() if SUBSPEC_RE.match(run[-1]) else None
                if run:
                    leftover, header = split_header(run)
                    if leftover:
                        attach_text(spec, last_field, leftover)
                    drink = {'name': header_name(header), 'template': header[0].endswith('(category)'),
                             'specs': []}
                    drinks.append(drink)
                spec = new_spec(marker)
                drink['specs'].append(spec)
                last_field = None
            else:
                attach_text(spec, last_field, run)
            continue
        if kind == 'bullet':
            spec['ingredients'].append(tokens[i][1])
        else:
            _, field, value = tokens[i]
            if field == 'ice':
                spec['fields']['glass'] = f"{spec['fields'].get('glass', '')} w/ {value}".strip()
            elif field == 'variations':
                spec['notes'].append(('Variations: ' + value).strip())
                field = 'notes'
            else:
                spec['fields'][field] = value
            last_field = field
        i += 1
    return drinks

def attach_text(spec, last_field, run):
    if spec is None or run[0].startswith('"') or run[0] == 'SUBJECT TO REVIEW':
        return  # long pull-quotes and editorial flags aren't recipe content
    if last_field == 'notes':                       # lines listed under "Variations:"
        sep = ' ' if spec['notes'][-1].endswith(':') else '; '
        spec['notes'][-1] += sep + '; '.join(run)
    elif run[0].startswith('*') or last_field is None:
        spec['notes'].append(' '.join(run).lstrip('*').strip())
    else:                                           # a field that wrapped onto more lines
        spec['fields'][last_field] = f"{spec['fields'].get(last_field, '')} {' '.join(run)}".strip()


# ── 4. Ingredient lines → qty / name / key ───────────────────────────────────
NUM = r'(?:\d+\s+\d+/\d+|\d+/\d+|\d*\.\s?\d+|\d+(?:\s*-\s*\d+)?)'
UNIT = r'(?:oz|ounces?|dash(?:es)?|drops?|tsp|teaspoons?|barspoons?)'
LEAD_QTY = re.compile(rf'^(?P<qty>{NUM}(?:\s*{UNIT}\b\.?)?(?:\s+float\b,?)?)\s*(?:of\s+)?(?P<name>.*)$', re.I)
NAME_FIXES = {'Absinth': 'Absinthe', 'Yellow Chartreuce': 'Yellow Chartreuse', 'Frambois': 'Framboise',
              'Laphroig': 'Laphroaig', 'Angostura Biters': 'Angostura Bitters'}
# Whole-line fixes for typos/shorthand in the PDF. None drops the line; a list splits it.
LINE_FIXES = {
    'Xxx tincture': None,                       # placeholder left in the source (Armed Robbery)
    '3 Dash Orange': '3 dash Orange Bitters',
    '4 D Angostura': '4 dash Angostura Bitters',
    '.25 0z Montenegro': '.25 oz Montenegro',
    'Top w Club': 'Top Club Soda',
    'Top W/ Champagne': 'Top Champagne',
    'Muddle Mint + Cucumber': ['Muddle Mint', 'Muddle Cucumber'],
}

def split_line(line):
    line = line.strip()
    if m := re.match(r'^float\s+(.*)$', line, re.I):          # "Float 0.5 oz Goslings"
        qty, name = split_line(m.group(1))
        return (qty + ' float').strip(), name
    if m := re.match(r'^(top|pinch|muddle)\s+(.*)$', line, re.I):
        return m.group(1).capitalize(), m.group(2)
    if m := re.match(r'^(tsp|teaspoon)\s+(.*)$', line, re.I):
        return '1 tsp', m.group(2)
    if m := re.match(r'^scant\s+(.*)$', line, re.I):
        qty, name = split_line(m.group(1))
        return qty, name + ' (scant)'
    if m := re.match(r'^(.*?)\s+(rinse|spray)\b.*$', line, re.I):   # "Absinthe rinse on glass"
        return m.group(2).capitalize(), m.group(1)
    if m := re.match(r'^(.*?)\s+float$', line, re.I):          # "Cream Float"
        return 'Float', m.group(1)
    m = LEAD_QTY.match(line)
    if m and m.group('name'):
        qty, name = m.group('qty'), m.group('name')
        if pm := re.match(r'^(\([^)]*\))\s*(.*)$', name):     # "0.5 oz (scant) Lime Juice"
            name = f'{pm.group(2)} {pm.group(1)}'
        return qty, name
    return '', line

def lookup_key(name):
    def norm(s):
        s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
        return re.sub(r'\s+', ' ', s).strip(' .,;:')
    aliases = CATALOG['aliases']
    for candidate in (name, re.sub(r'\s*\(.*?\)\s*', ' ', name)):
        if norm(candidate) in aliases:
            return aliases[norm(candidate)]
    return None


# ── 5. Normalizers ───────────────────────────────────────────────────────────
COUNT_WORDS = re.compile(r'egg|wheel|lea(f|ves)|wedge|segment|cube|slice|sprig', re.I)
COUNT_KEYS = {'egg_white', 'whole_egg', 'mint', 'cucumber', 'raspberry', 'sugar'}
TOPPERS = {'seltzer', 'sparkling', 'beer', 'coke'}

def fmt_number(n):
    n = re.sub(r'\.\s+', '.', n.strip())
    if ' ' in n:
        whole, frac = n.split()
        v = int(whole) + Fraction(frac)
    else:
        v = Fraction(n)
    return f'{float(v):.3f}'.rstrip('0').rstrip('.')

def norm_qty(q, key, name):
    q = q.strip().rstrip(',').strip()
    if not q:
        return 'Top' if key in TOPPERS else ('1' if key in ('egg_white', 'whole_egg') else '')
    if m := re.fullmatch(rf'({NUM})\s*oz\.?,?\s*(float)?', q, re.I):
        return f'{fmt_number(m.group(1))} oz' + (' float' if m.group(2) else '')
    if m := re.fullmatch(r'(\d+)\s*-\s*(\d+)', q):
        return f'{m.group(1)}-{m.group(2)}'
    if m := re.fullmatch(NUM, q):
        if '.' in q or (key not in COUNT_KEYS and not COUNT_WORDS.search(name)):
            return f'{fmt_number(q)} oz'   # bare "0.75 Lime" means ounces
        return q
    if m := re.fullmatch(r'(\d+)?\s*(dash|dashes|drop|drops)', q, re.I):
        n, unit = int(m.group(1) or 1), 'dash' if m.group(2).lower().startswith('dash') else 'drop'
        return f'{n} {unit}' + ('' if n == 1 else 'es' if unit == 'dash' else 's')
    if m := re.fullmatch(r'(\d+)?\s*(tsp|teaspoon)', q, re.I):
        return f'{m.group(1) or 1} tsp'
    return q[0].upper() + q[1:]

def norm_name(n):
    n = NAME_FIXES.get(n, n)
    if n == n.lower():
        n = ' '.join(w[0].upper() + w[1:] for w in n.split())
    return n

def norm_method(m):
    parts = [p.strip() for p in re.split(r'(?<!\bw)/', m) if p.strip()]
    m = ' / '.join(p[0].upper() + p[1:] for p in parts)
    return re.sub(r'\bw/\s*(\w)', lambda x: 'w/ ' + x.group(1).upper(), m)

GLASS_EXPLICIT = {
    'Absinthe rinsed Nick & Nora': 'Nick & Nora (Absinthe Rinse)',
    'Absinthe rinsed Single Old Fashioned/ Neat': 'Old Fashioned, Neat (Absinthe Rinse)',
    'Nick & Nora w/ Campari Rinse': 'Nick & Nora (Campari Rinse)',
    'Coupe Top W/ Bubbles': 'Coupe, Topped w/ Bubbles',
    'Rocks w/ Block': 'Old Fashioned w/ Block',
    'Double': 'Double Old Fashioned',
    'Fizz': 'Fizz Glass',
    'Old Fashioned w/ KD (Nick & Nora if requested up)': 'Old Fashioned w/ Kold Draft (Nick & Nora if up)',
}

def norm_glass(g):
    g = re.sub(r'\s+', ' ', g).strip().replace('R ock', 'Rock')
    if g in GLASS_EXPLICIT:
        return GLASS_EXPLICIT[g]
    g = re.sub(r'^N\+N$|N[iI]c?k?\s*(?:\+|&|and)\s*Nora', 'Nick & Nora', g)
    g = re.sub(r'\bOF\b', 'Old Fashioned', g)
    g = re.sub(r'\bKD\b', 'Kold Draft', g)
    if '/' in g and 'w/' not in g:
        g = re.sub(r'\s*/\s*', ' w/ ', g)
    g = re.sub(r'\s+with\s+', ' w/ ', g, flags=re.I)
    g = re.sub(r'w/(?=\S)', 'w/ ', g)
    if not g.startswith(('Fizz', 'Toddy', 'Water')):
        g = re.sub(r' Glass(?= w/|$)', '', g)
    g = re.sub(r'\b(Crushed|Block) ice\b', r'\1', g, flags=re.I)
    g = re.sub(r'w/ (\w)', lambda m: 'w/ ' + m.group(1).upper(), g)
    g = re.sub(r'\b(cubes|crushed|rock|glass|tin)\b', lambda m: m.group(1).capitalize(), g)
    return g


# ── 6. OVERRIDES: things the PDF doesn't say (or says in the wrong place) ──
# Which landing card(s) a drink shows on. Default: every spirit group whose
# ingredients it contains; vermouth/sherry/amaro-led drinks with no big spirit → lowabv.
GROUPS = {
    'gin': {'gin', 'old_tom_gin', 'navy_gin', 'genever'},
    'whiskey': {'bourbon', 'rye', 'scotch', 'peated_scotch', 'irish_whiskey', 'japanese_whisky'},
    'tequila': {'tequila', 'reposado_tequila', 'anejo_tequila', 'mezcal'},
    'rum': {'rum', 'white_rum', 'aged_rum', 'black_rum', 'jamaican_rum', 'cachaca', 'agricole', 'arrack'},
    'vodka': {'vodka'},
    'brandy': {'cognac', 'brandy', 'pisco', 'apple_brandy'},
}
LOW_ABV_KEYS = {'dry_vermouth', 'sweet_vermouth', 'blanc_vermouth', 'vermouth', 'sherry', 'lillet_blanc',
                'lillet_rouge', 'cocchi_americano', 'cocchi', 'campari', 'aperol', 'cynar', 'fernet',
                'montenegro', 'cio_ciaro', 'china_china', 'suze', 'amaro', 'punt_e_mes', 'nonino', 'ramazzotti'}
BASE_OVERRIDES = {
    **{n: ['lowabv'] for n in ('Bellini', 'St. Germain Cocktail', 'Carajillo', 'Duke', 'NA Mule')},
    **{n: ['aquavit'] for n in ('Dandelion & Burdock', 'Trident', 'Yellow Parrot', 'Death in the Afternoon')},
}
# Drink families. Results are grouped by these, and each family is bright or bold.
# First matching rule wins; FAMILY_OVERRIDES fixes the misfits.
CITRUS = {'lemon_juice', 'lime_juice', 'orange_juice', 'grapefruit_juice', 'pineapple', 'cranberry'}
TOPPERS = {'seltzer', 'sparkling', 'beer', 'coke'}
FIZZ_RE = re.compile(r'seltzer|soda|sparkling|champagne|bubbles|cola|\bbeer\b|\bipa\b', re.I)
CREAMY_KEYS = {'cream', 'whole_egg', 'butter', 'coffee_liqueur', 'cold_brew', 'espresso_batch'}
TROPICAL_KEYS = {'pineapple', 'passion_fruit', 'falernum', 'allspice_dram', 'banana_liqueur'}
BITTER_KEYS = {'campari', 'aperol', 'cynar', 'fernet', 'montenegro', 'cio_ciaro', 'china_china', 'suze',
               'amaro', 'nonino', 'ramazzotti', 'punt_e_mes'}
FORTIFIED_KEYS = {'dry_vermouth', 'sweet_vermouth', 'blanc_vermouth', 'vermouth', 'sherry', 'lillet_blanc',
                  'lillet_rouge', 'cocchi_americano', 'cocchi'}
FAMILY_STYLE = {'sour': 'bright', 'highball': 'bright', 'tropical': 'bright', 'julep': 'bright',
                'old_fashioned': 'bold', 'stirred': 'bold', 'bitter': 'bold', 'creamy': 'bold'}
FAMILY_OVERRIDES = {'Southern Belle': 'julep'}

def oz(qty):
    m = re.match(r'([\d.]+) oz', qty)
    return float(m.group(1)) if m else 0

def family_of(name, ingredients, method, glass):
    if name in FAMILY_OVERRIDES:
        return FAMILY_OVERRIDES[name]
    keys = {i['key'] for i in ingredients}
    citrus_oz = sum(oz(i['qty']) for i in ingredients if i['key'] in CITRUS)
    shaken = re.search(r'shake|whip', method, re.I)
    if keys & CREAMY_KEYS and not keys & CITRUS:
        return 'creamy'
    if keys & TOPPERS or FIZZ_RE.search(method):
        return 'highball'
    if (keys & TROPICAL_KEYS or ('orgeat' in keys and keys & GROUPS['rum'])) and (keys & CITRUS or shaken):
        return 'tropical'
    if 'mint' in keys and re.search(r'whip|dump|swizzle|crushed|pebble|julep|build', f'{method} {glass}', re.I):
        return 'julep'
    if keys & CITRUS and (shaken or citrus_oz >= 0.5):
        return 'sour'
    if keys & BITTER_KEYS:
        return 'bitter'
    if keys & FORTIFIED_KEYS:
        return 'stirred'
    return 'old_fashioned'

# Flavor tags for the filter chips. Tropical/fizzy also follow the family.
FLAVORS = {
    'bitter': BITTER_KEYS,
    'smoky': {'mezcal', 'peated_scotch'},
    'herbal': {'green_chartreuse', 'yellow_chartreuse', 'absinthe', 'benedictine', 'mint', 'cucumber',
               'drambuie', 'galliano', 'aquavit', 'creme_menthe', 'aloe'},
    'floral': {'st_germain', 'creme_violette', 'orange_flower'},
    'fruity': {'mure', 'cassis', 'raspberry', 'cherry_heering', 'apricot_liqueur', 'peche', 'creme_peche',
               'pear', 'grenadine', 'cranberry', 'pamplemousse'},
    'tropical': TROPICAL_KEYS | {'pine_gum'},
    'spicy': {'ginger', 'ancho_reyes', 'hot_sauce', 'cinnamon_syrup', 'allspice_dram'},
    'creamy': {'cream', 'egg_white', 'whole_egg', 'butter'},
    'fizzy': TOPPERS,
    'coffee': {'coffee_liqueur', 'cold_brew', 'espresso_batch', 'creme_cacao'},
}
FAMILY_FLAVOR = {'tropical': 'tropical', 'highball': 'fizzy'}

# Drinks most people have heard of. They're shown first in their family with a "Classic" badge.
CLASSICS = {
    'Old Fashioned', 'Manhattan', 'Perfect Manhattan', 'Black Manhattan', 'Martini', 'Dirty Martini', 'Martinez',
    'Negroni', 'Boulevardier', 'Americano', 'Old Pal', 'Daiquiri', 'Hemingway Daiquiri', 'Margarita', 'Paloma',
    'Mojito', 'Moscow Mule', 'Dark and Stormy', 'Whiskey Sour (egg white)', 'Whiskey Sour (No egg white)',
    'Gimlet', 'French 75', 'Sidecar', 'Mai Tai', 'Mint Julep', 'Sazerac', 'Vieux Carre', 'Aviation', 'Last Word',
    'Penicillin', 'Paper Plane', 'Espresso Martini', 'Cosmopolitan', 'Pisco Sour', 'Bees Knees', 'Gold Rush',
    'Clover Club', 'Corpse Reviver No. 2', 'Caipirinha', 'Piña Colada (Classic)', 'Long Island Iced Tea',
    'White Russian', 'Hot Toddy', 'Brandy Alexander', 'Jungle Bird', 'Zombie', 'Hurricane', 'Singapore Sling',
    'Ramos Gin Fizz', 'Tequila Sunrise', 'Kamikaze', 'Rusty Nail', 'Stinger', 'Jack Rose', 'Blood & Sand',
    'Brown Derby', 'Hanky Panky', 'Bijou', 'Oaxacan Old Fashioned', 'Naked & Famous', 'Ward 8', 'Pimm’s Cup',
    'Bellini', 'Egg Nog', 'Hot Buttered Rum', 'Gin Gin Mule', 'El Diablo', 'Pegu Club Cocktail', 'Brooklyn',
    'White Lady', 'Pink Lady', 'Bramble', 'Southside', 'Harvey Wallbanger', 'Blue Hawaiian', 'Tom Collins',
}
# The household names among them. These lead their section ("Sours, like a Whiskey Sour").
ICONIC = {
    'Old Fashioned', 'Manhattan', 'Martini', 'Negroni', 'Daiquiri', 'Margarita', 'Mojito', 'Moscow Mule',
    'Whiskey Sour (No egg white)', 'Gimlet', 'French 75', 'Sidecar', 'Mai Tai', 'Mint Julep', 'Cosmopolitan',
    'Espresso Martini', 'Piña Colada (Classic)', 'Paloma', 'Dark and Stormy', 'Long Island Iced Tea',
    'White Russian', 'Tequila Sunrise', 'Bellini', 'Americano', 'Pisco Sour', 'Caipirinha', 'Hot Toddy',
}
# Keep names stable where the PDF's title and heading disagree (favorites are saved by name).
RENAMES = {'Alone In the Dark': 'Alone In The Dark'}
# Credits / notes that the PDF typed straight after the garnish.
FIELD_OVERRIDES = {
    'Infante': {'garnish': 'Grated Nutmeg',
                'origin': 'Giuseppe González, Dutch Kills, 2009. Named after Pedro Infante, singer and actor '
                          'from the golden age of Mexican cinema.'},
    'Latin Quarter': {'garnish': 'Lemon Twist (discarded)', 'origin': 'Joaquín Simó, Death & Co, 2008'},
}
TEXT_FIXES = {'Guiseppe Gonzalez': 'Giuseppe González', 'Bellevue Straford': 'Bellevue-Stratford',
              'Lemon Twit': 'Lemon Twist', '&Lemon': '& Lemon'}

def espresso_martini(drink, main, alt):
    # The house pour is a 4.25 oz batch; the "another base spirit" variant shows it's
    # 1.5 oz spirit + 2.75 oz N/A espresso batch. Vodka is assumed as the default base.
    main['ingredients'] = ['1.5 oz Vodka'] + alt['ingredients'][1:]
    main['notes'] = ['House pour is a 4.25 oz pre-batch (vodka assumed as the default base). '
                     'If a guest calls for another base spirit, use 1.5 oz of it with the N/A espresso batch.'] \
                    + alt['notes']
    return main

def resolve(drink):
    """Pick the spec to publish when a drink has several (service batch, single, variant)."""
    specs = {(s['label'] or '').lower(): s for s in drink['specs']}
    if drink['name'] == 'Espresso Martini':
        alt = next(s for s in drink['specs'] if (s['label'] or '').startswith('*If guest'))
        return espresso_martini(drink, specs[''], alt)
    main = specs.get('single recipe') or drink['specs'][0]
    for s in drink['specs']:           # fields missing from the chosen spec come from its siblings
        for k, v in s['fields'].items():
            main['fields'].setdefault(k, v)
        if s is not main and not (s['label'] or '').lower().endswith('batch recipe'):
            main['notes'] += [n for n in s['notes'] if n not in main['notes']]
    return main


# ── 7. Build ─────────────────────────────────────────────────────────────────
def build(pdf_path):
    drinks = parse(tokenize(pdf_lines(pdf_path)))
    cocktails, unmapped = [], Counter()
    for drink in drinks:
        spec = resolve(drink)
        name = drink['name']
        fields = {k: spec['fields'].get(k, '') for k in ('method', 'glass', 'garnish', 'origin')}
        fields.update(FIELD_OVERRIDES.get(name, {}))
        notes = ' '.join(spec['notes'])
        name = RENAMES.get(name, name)
        for bad, good in TEXT_FIXES.items():
            for f in ('garnish', 'origin'):
                fields[f] = fields[f].replace(bad, good)

        ingredients = []
        lines = []
        for line in spec['ingredients']:
            fixed = LINE_FIXES.get(line, line)
            lines += fixed if isinstance(fixed, list) else [] if fixed is None else [fixed]
        for line in lines:
            qty, ing_name = split_line(line)
            ing_name = norm_name(ing_name)
            key = lookup_key(ing_name)
            if not key:
                unmapped[ing_name] += 1
                continue
            ingredients.append({'raw': line, 'qty': norm_qty(qty, key, ing_name), 'name': ing_name, 'key': key,
                                'display': ING[key]['display'], 'category': ING[key]['category']})

        keys = {i['key'] for i in ingredients}
        drink['template'] = drink['template'] or 'any_spirit' in keys   # "2 oz Spirit" = make it with anything
        if drink['template']:
            base = list(GROUPS)
        else:
            base = BASE_OVERRIDES.get(name) or [g for g, ks in GROUPS.items() if keys & ks]
            if not base and keys & LOW_ABV_KEYS:
                base = ['lowabv']
        method, glass = norm_method(fields['method']), norm_glass(fields['glass'])
        family = family_of(name, ingredients, method, glass)
        flavors = [f for f, ks in FLAVORS.items()
                   if keys & ks or FAMILY_FLAVOR.get(family) == f or (f == 'fizzy' and FIZZ_RE.search(method))]

        c = {'name': name, 'method': method, 'glass': glass, 'garnish': fields['garnish'], 'origin': fields['origin']}
        if notes:
            c['notes'] = notes
        c.update(family=family, style=FAMILY_STYLE[family], flavors=flavors, base=base)
        if name in CLASSICS and not drink['template']:
            c['classic'] = True
            if name in ICONIC:
                c['iconic'] = True
        if drink['template']:
            c['template'] = True
        c['ingredients'] = ingredients
        cocktails.append(c)

    # Templates (Collins, Fix, …) whose name clashes with a real recipe get a suffix.
    real_names = {c['name'] for c in cocktails if not c.get('template')}
    for c in cocktails:
        if c.get('template'):
            if c['name'] in real_names:
                c['name'] += ' (Any Spirit)'
            c['notes'] = (c.get('notes', '') + ' A template: make it with whatever spirit you like.').strip()

    if unmapped:
        sys.exit('Unmapped ingredient names. Add aliases to tools/ingredients.json:\n  '
                 + '\n  '.join(f'{n!r} ×{k}' for n, k in unmapped.most_common()))
    problems = [c['name'] for c in cocktails if not c['base'] or not c['ingredients']]
    dupes = [n for n, k in Counter(c['name'] for c in cocktails).items() if k > 1]
    if problems or dupes:
        sys.exit(f'No landing card for: {problems}\nDuplicate names: {dupes}')

    used = {i['key'] for c in cocktails for i in c['ingredients']}
    data = {'cocktails': cocktails,
            'ingredients': [i for i in CATALOG['ingredients'] if i['key'] in used]}
    out = ROOT / 'cocktails-data.js'
    out.write_text('window.COCKTAIL_DATA = ' + json.dumps(data, indent=2, ensure_ascii=True) + ';\n',
                   encoding='utf-8')
    print(f'Wrote {len(cocktails)} drinks, {len(data["ingredients"])} ingredients → {out.relative_to(ROOT)}')
    print('Per landing card:', dict(Counter(b for c in cocktails for b in c['base'])))
    print('Style:', dict(Counter(c['style'] for c in cocktails)))
    print('Family:', dict(Counter(c['family'] for c in cocktails)))
    print('Flavor:', dict(Counter(f for c in cocktails for f in c['flavors'])))
    missing = (CLASSICS | ICONIC) - {c['name'] for c in cocktails}
    if missing:
        print('Not in the PDF (ignored):', ', '.join(sorted(missing)))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(sys.argv[1])
