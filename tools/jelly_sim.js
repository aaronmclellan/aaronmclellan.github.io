// Difficulty check for jelly.html. Runs the page's CORE block (rules, level maker and
// bots, no DOM), builds each level and plays it with the page's bots:
//   smart:   keeps a slot spare (one jelly at a time, then two); shows a careful player can win
//   busy:    up to 3 on the track, always the jelly with the most to shoot
//   good:    up to 2 on the track, plays like smart but slips one move in seven (30 games)
//   casual:  up to 3 on the track, the most useful jelly 70% of the time (30 games, fresh seeds)
//   random:  up to 3 on the track, any jelly (30 games)
// `aim` is the good-player win rate the level maker landed on and the band it aims for.
//
//   node tools/jelly_sim.js [from] [to]
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(process.env.PAGE || path.join(__dirname, '..', 'jelly.html'), 'utf8');
const core = html.split('/* CORE START')[1].split('/* CORE END */')[0].replace(/^[^\n]*\n/, '');
const api = new Function(core + `
return {PICS, levelInfo, makeLevel, botPlay, mulberry32};`)();

const pct = v => v == null ? '    -' : (Math.round(v * 100) + '%').padStart(5);
const from = +(process.argv[2] || 1), to = +(process.argv[3] || api.PICS.length);
console.log('lvl pic           size  col jel lines hid ice slot |   make tries aim       | smart     busy       good casual random');
let total = 0;
for (let n = from; n <= to; n++) {
    const info = api.levelInfo(n);
    const t0 = Date.now(), L = api.makeLevel(n), ms = Date.now() - t0;
    total += ms;
    const colors = new Set(L.col.filter(Boolean)).size;
    const hid = L.jel.filter(j => j.hid).length, ice = L.hp.filter(v => v > 1).length;
    const r = api.mulberry32(n);
    const sm = [api.botPlay(L, 'smart', 1, r), api.botPlay(L, 'smart', 2, r)].find(x => x.won) || api.botPlay(L, 'smart', 1, r);
    const busy = api.botPlay(L, 'best', 3, r);
    let cw = 0, rw = 0, gw = 0;
    for (let k = 0; k < 30; k++) {
        if (api.botPlay(L, 'good', 2, api.mulberry32(7000 + n * 97 + k)).won) gw++;
        if (api.botPlay(L, 'casual', 3, api.mulberry32(5000 + n * 97 + k)).won) cw++;
        if (api.botPlay(L, 'random', 3, api.mulberry32(9000 + n * 97 + k)).won) rw++;
    }
    const res = x => (x.won ? `won ${x.t.toFixed(0)}s` : x.over || 'stalled').padEnd(9);
    const aim = (info.band ? `${pct(L.rate)} ${info.band.map(v => Math.round(v * 100)).join('-')}` : 'tutorial').padEnd(9);
    console.log(`${String(n).padStart(3)} ${(info.pic.id + (info.mirror ? '~' : '') + (info.hard ? '!' : '')).padEnd(13)} ${(L.w + 'x' + L.h).padEnd(5)} ${String(colors).padStart(3)} ${String(L.jel.length).padStart(3)} ${String(L.qs.length).padStart(5)} ${String(hid).padStart(3)} ${String(ice).padStart(3)} ${String(L.slots).padStart(4)} | ${String(ms).padStart(4)}ms ${String(L.tries || '').padStart(3)} ${aim} | ${res(sm)} ${res(busy)} ${pct(gw / 30)} ${pct(cw / 30)} ${pct(rw / 30)}`);
}
console.log(`level making took ${total} ms in all`);
