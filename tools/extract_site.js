const fs = require('fs'), path = require('path');
const dir = process.argv[2];
function objAfter(src, marker) {
  const i = src.indexOf(marker); if (i < 0) return null;
  let j = src.indexOf('{', i), depth = 0, k = j, q = null;
  for (; k < src.length; k++) { const c = src[k];
    if (q) { if (c === '\\') k++; else if (c === q) q = null; continue; }
    if (c === "'" || c === '"' || c === '`') q = c; else if (c === '{') depth++; else if (c === '}') { depth--; if (!depth) break; } }
  return eval('(' + src.slice(j, k + 1) + ')');
}
const out = {};
out.app = objAfter(fs.readFileSync(path.join(dir, 'app.js'), 'utf8'), 'const T = {');
for (const f of fs.readdirSync(dir).filter(f => f.endsWith('.html') && f !== 'dev.html')) {
  const o = objAfter(fs.readFileSync(path.join(dir, f), 'utf8'), 'const S = {'); if (o) out[f.replace('.html', '')] = o;
}
console.log(JSON.stringify(out));
