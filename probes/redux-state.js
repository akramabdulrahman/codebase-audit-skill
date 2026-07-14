// v2 REDUX-STATE extractor (Babel AST) — the "and back" half: thunk -> action type -> reducer -> state slice.
// Emits:
//   slices     : {sliceName: reducerLocalName}                combineReducers({slice: reducer}) = the state tree
//   typeSlice  : {ACTION_TYPE: [sliceName]}                   which state slice(s) react to each action type
//   thunkTypes : {thunkName: [ACTION_TYPE]}                   the action types each thunk dispatches
//   thunkSlices: {thunkName: [sliceName]}                     resolved: which state slices a thunk writes
// Usage: node redux-state.js <clientDir>  ->  JSON
const path = require("path"), fs = require("fs");
const clientDir = process.argv[2] || process.cwd();
const { parser, traverse } = require(path.join(__dirname, "_babel"))(clientDir);
const parse = (f) => parser.parse(fs.readFileSync(f, "utf8"), { sourceType: "module", plugins: ["jsx", "objectRestSpread", "optionalChaining", "nullishCoalescingOperator", "classProperties", "asyncGenerators"] });
function listFiles(dir) {
  const out = []; if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
    if (e.name === "node_modules" || e.name.startsWith(".")) continue;
    const f = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...listFiles(f));
    else if (/\.jsx?$/.test(e.name)) out.push(f);
  }
  return out;
}
const CFG = (() => { try { return JSON.parse(fs.readFileSync(process.env.AUDIT_CONFIG, "utf8")).knobs || {}; } catch (_) { return {}; } })();
const knob = (k, d) => (CFG[k] === undefined ? d : CFG[k]);
const REDUCER_DIR = knob("reducer_dir", "src/reducers");
const ACTION_DIRS = knob("action_dirs", ["src/actions"]);
const reducerRoot = path.join(clientDir, REDUCER_DIR);
function resolveImport(fromFile, spec) {
  if (!spec.startsWith(".")) return null;
  const base = path.resolve(path.dirname(fromFile), spec);
  for (const c of [base, base + ".js", base + ".jsx", path.join(base, "index.js"), path.join(base, "index.jsx")])
    try { if (fs.statSync(c).isFile()) return c; } catch (_) {}
  return null;
}

// ---- combineReducers({ slice: reducerLocal }) — resolve slice -> the reducer FILE via imports (precise) ----
const slices = {};                 // sliceName -> reducerLocalName
const sliceToFile = {};            // sliceName -> resolved reducer file (authoritative)
const fileToSlices = {};           // reducer file -> [sliceName]
for (const f of listFiles(reducerRoot)) {
  let ast; try { ast = parse(f); } catch (_) { continue; }
  const imp = {};                  // localName -> resolved file (imports in THIS file, e.g. reducers/index.js)
  traverse(ast, {
    ImportDeclaration(p) { const r = resolveImport(f, p.node.source.value); if (r) p.node.specifiers.forEach((s) => s.local && (imp[s.local.name] = r)); },
    VariableDeclarator(p) { const i = p.node.init; if (i && i.type === "CallExpression" && i.callee.name === "require" && i.arguments[0] && p.node.id.name) { const r = resolveImport(f, i.arguments[0].value || ""); if (r) imp[p.node.id.name] = r; } },
  });
  traverse(ast, {
    CallExpression(p) {
      if (p.node.callee.name !== "combineReducers" || !p.node.arguments[0] || p.node.arguments[0].type !== "ObjectExpression") return;
      for (const pr of p.node.arguments[0].properties) {
        if (!pr.key) continue;
        const slice = pr.key.name;
        const reducer = pr.value.type === "Identifier" ? pr.value.name : slice;  // shorthand `confirmRegistration`
        slices[slice] = reducer;
        const file = imp[reducer] || null;                                        // the reducer's actual file
        if (file) { sliceToFile[slice] = file; (fileToSlices[file] ||= []).push(slice); }
      }
    },
  });
}

// ---- each reducer file: its `case` types -> ONLY the slice(s) that file actually feeds ----
const typeSlice = {};               // ACTION_TYPE -> Set(slice)
for (const f of listFiles(reducerRoot)) {
  let ast; try { ast = parse(f); } catch (_) { continue; }
  const cases = [];
  traverse(ast, { SwitchCase(p) { const t = p.node.test; if (t && t.type === "Identifier") cases.push(t.name); else if (t && t.type === "StringLiteral") cases.push(t.value); } });
  if (!cases.length) continue;
  // authoritative: slices whose combineReducers import IS this file; fallback to basename only if unresolved
  let sliceNames = fileToSlices[f];
  if (!sliceNames) { const b = path.basename(f, ".js").replace(/Reducer$/, ""); sliceNames = slices[b] ? [b] : [b]; }
  for (const t of cases) { (typeSlice[t] ||= new Set()); sliceNames.forEach((s) => typeSlice[t].add(s)); }
}

// ---- each thunk (action dirs): the action types it dispatches ----
const thunkTypes = {};              // thunkName -> Set(type)
const actionFiles = ACTION_DIRS.flatMap((d) => listFiles(path.join(clientDir, d)));
for (const f of actionFiles) {
  let ast; try { ast = parse(f); } catch (_) { continue; }
  traverse(ast, {
    ExportNamedDeclaration(p) {
      const d = p.node.declaration; if (!d) return;
      const decls = d.type === "VariableDeclaration" ? d.declarations : d.type === "FunctionDeclaration" ? [{ id: d.id, node: d }] : [];
      for (const de of decls) {
        if (!de.id || !de.id.name) continue;
        const types = new Set();
        traverse(de.init || de.node || de, {
          noScope: true,
          CallExpression(q) {
            if (q.node.callee.name === "dispatch" && q.node.arguments[0] && q.node.arguments[0].type === "ObjectExpression") {
              for (const pr of q.node.arguments[0].properties)
                if (pr.key && pr.key.name === "type") { const v = pr.value; if (v.type === "Identifier") types.add(v.name); else if (v.type === "StringLiteral") types.add(v.value); }
            }
          },
        }, {});
        if (types.size) thunkTypes[de.id.name] = [...types];
      }
    },
  });
}
const thunkSlices = {};
for (const [thunk, types] of Object.entries(thunkTypes)) {
  const s = new Set();
  for (const t of types) for (const sl of (typeSlice[t] || [])) s.add(sl);
  thunkSlices[thunk] = [...s];
}

const ser = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, [...v]]));
process.stdout.write(JSON.stringify({
  slices, typeSlice: ser(typeSlice), thunkTypes, thunkSlices,
  stats: { slices: Object.keys(slices).length, actionTypes: Object.keys(typeSlice).length,
           thunksWithTypes: Object.keys(thunkTypes).length },
}));
