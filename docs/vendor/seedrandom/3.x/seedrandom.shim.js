// ESM shim for `seedrandom` — @flowmap.gl/data imports ONLY the named `alea`
// export (`import { alea } from 'seedrandom'` in FlowmapSelectors.js). The upstream
// npm package is CommonJS (module.exports), which a classic <script type=module>
// import cannot consume, so we vendor the self-contained `alea` implementation
// here verbatim and publish it as a native ESM named export. Zero CDN, same-origin.
//
// Source: seedrandom 3.x `lib/alea.js` — a port of Johannes Baagøe's Alea PRNG.
// Original work © 2010 Johannes Baagøe, MIT License (see the license text below).
// seedrandom itself is MIT (© 2019 David Bau). No dependencies; deterministic.
//
//   Permission is hereby granted, free of charge, to any person obtaining a copy
//   of this software and associated documentation files (the "Software"), to deal
//   in the Software without restriction... THE SOFTWARE IS PROVIDED "AS IS".

function Alea(seed) {
  var me = this, mash = Mash();
  me.next = function() {
    var t = 2091639 * me.s0 + me.c * 2.3283064365386963e-10; // 2^-32
    me.s0 = me.s1;
    me.s1 = me.s2;
    return me.s2 = t - (me.c = t | 0);
  };
  me.c = 1;
  me.s0 = mash(' ');
  me.s1 = mash(' ');
  me.s2 = mash(' ');
  me.s0 -= mash(seed);
  if (me.s0 < 0) { me.s0 += 1; }
  me.s1 -= mash(seed);
  if (me.s1 < 0) { me.s1 += 1; }
  me.s2 -= mash(seed);
  if (me.s2 < 0) { me.s2 += 1; }
  mash = null;
}

function copy(f, t) {
  t.c = f.c;
  t.s0 = f.s0;
  t.s1 = f.s1;
  t.s2 = f.s2;
  return t;
}

function impl(seed, opts) {
  var xg = new Alea(seed),
      state = opts && opts.state,
      prng = xg.next;
  prng.int32 = function() { return (xg.next() * 0x100000000) | 0; };
  prng.double = function() {
    return prng() + (prng() * 0x200000 | 0) * 1.1102230246251565e-16; // 2^-53
  };
  prng.quick = prng;
  if (state) {
    if (typeof(state) == 'object') copy(state, xg);
    prng.state = function() { return copy(xg, {}); };
  }
  return prng;
}

function Mash() {
  var n = 0xefc8249d;
  var mash = function(data) {
    data = String(data);
    for (var i = 0; i < data.length; i++) {
      n += data.charCodeAt(i);
      var h = 0.02519603282416938 * n;
      n = h >>> 0;
      h -= n;
      h *= n;
      n = h >>> 0;
      h -= n;
      n += h * 0x100000000; // 2^32
    }
    return (n >>> 0) * 2.3283064365386963e-10; // 2^-32
  };
  return mash;
}

const alea = impl;
export { alea };
export default { alea };
