// Balance check for ants.html. Runs the page's CORE block (the colony model,
// no DOM) with a bot that always buys the cheapest upgrade, then prints when
// the milestones land and how a run of flights from the nest paces out.
//
//   node tools/ants_sim.js [seed] [extra JS to run after CORE, e.g. "BAL.dig=0.03"]
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'ants.html'), 'utf8');
const core = html.split('/* CORE START')[1].split('/* CORE END */')[0].replace(/^[^\n]*\n/, '');
const seed = +(process.argv[2] || 1);
const api = new Function(core + '\n' + (process.argv[3] || '') + `
return {
    init(seed, prev) { rng = mulberry32(seed); st = freshState(prev); st.seed = seed; genSoil(seed); foundNest(); },
    st: () => st, nest, econ, buy, cheapestBuy, rates, queenGain, canFly, checkAch,
    onRaid(fn) { onRaid = fn; },
    // Weather follows the clock, so the sim keeps its own.
    setNow(ms) { simNow = ms; },
};`)();
const T0 = Date.UTC(2026, 9, 13, 14);   // a fixed October afternoon, so runs repeat
const mins = t => (t / 60).toFixed(1).padStart(6) + 'm';
const greedy = () => {
    const s = api.st();
    // A new farm only has Picnic crumbs: buy it once to open the rest, as a
    // player would, and put ants on guard once raiders turn up.
    if (s.unlocked.length === 1) api.buy('click');
    if (s.unlocked.includes('guard') && !s.w.guard) s.w = {forage:50, dig:30, nurse:10, guard:10};
    for (let k = 0, id; k < 20 && (id = api.cheapestBuy()); k++) api.buy(id);
};
// Raids: the bot keeps the default share of guards and counts how fights go.
const raids = {won:0, lost:0, rain:0, ants:0, food:0};
api.onRaid(o => { if (o.rain) raids.rain++; else if (o.win) raids.won++; else { raids.lost++; raids.food += o.food; } raids.ants += o.ants; });
const raidLine = () => `raids: ${raids.won} won, ${raids.lost} lost, ${raids.rain} rained off, ${raids.ants.toFixed(1)} ants lost`;
const tick = t => { api.setNow(T0 + t * 1000); api.econ(1); greedy(); if (t % 5 === 0) api.checkAch(); };

console.log('First farm');
api.init(seed);
const marks = [
    ['50 ants', () => api.st().ants >= 50],
    ['120 ants', () => api.st().ants >= 120],
    ['first queen ready', () => api.canFly()],
    ['sugar cube', () => api.st().tier >= 2],
    ['honey drop', () => api.st().tier >= 4],
    ['nest fully dug', () => api.nest.full],
];
for (let t = 1; t <= 6 * 3600 && marks.length; t++) {
    tick(t);
    for (let k = marks.length - 1; k >= 0; k--) if (marks[k][1]()) { console.log(`${mins(t)}  ${marks[k][0]}`); marks.splice(k, 1); }
}
console.log(raidLine());
Object.assign(raids, {won:0, lost:0, rain:0, ants:0, food:0});

console.log('\nFlying whenever it at least doubles the queens');
api.init(seed);
for (let t = 1, start = 0; t <= 24 * 3600; t++) {
    tick(t);
    const st = api.st(), gain = api.queenGain();
    if (api.canFly() && gain >= Math.max(1, st.queens) && t - start > 600) {
        console.log(`${mins(t)}  farm ${st.colony + 1} flew after ${mins(t - start).trim()} with ${Math.floor(st.ants)} ants, ${st.queens + gain} queens now`);
        api.init(seed + st.colony + 1, {...st, queens:st.queens + gain});
        start = t;
    }
}
console.log(raidLine());
