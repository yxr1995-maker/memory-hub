const fs = require('fs');
const path = require('path');
const base = path.join(__dirname, 'dashboard/node_modules');
const {compile} = require(path.join(base,'@tailwindcss/node/dist/index.js'));
const html = fs.readFileSync(path.join(__dirname, 'dashboard.html'),'utf8');
const classes = new Set();
for (const match of html.matchAll(/class="([^"]+)"/g)) match[1].split(/\s+/).forEach(x=>classes.add(x));
for (const match of html.matchAll(/'([^'\n]*)'/g)) if (/\b(?:text-|bg-|p-|border-|rounded|space-|font-|w-|flex|hidden)/.test(match[1])) match[1].split(/\s+/).forEach(x=>classes.add(x));
(async()=>{
 const compiler = await compile('@import "tailwindcss";', {base:path.join(base,'tailwindcss'),onDependency(){}});
 fs.writeFileSync(path.join(__dirname, 'dashboard.css'),compiler.build([...classes]));
})();
