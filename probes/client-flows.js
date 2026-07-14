// v2 CLIENT-FLOWS extractor (Babel AST) for React-Router + classic-Redux-thunk + axios SPA.
// The real chain is  page -> connect({thunk}) / dispatch(thunk()) -> thunk in src/actions -> axios(url).
// So we extract at THUNK granularity (not file BFS, which leaks the whole actions barrel):
//   thunks          : [{file, name, calls:[{method,url}]}]         action-creator -> backend calls (url ${x}->:x)
//   componentThunks : [{file, component, thunks:[name]}]           page -> thunks it binds (connect obj / dispatch(name())
//   routes          : [{path, roles[], flags[], component}]        route -> role -> page
//   imports         : {file: {localName: resolvedFile}}            (name resolution aid)
// Downstream analyzers (intersect.py / actor-model.py) join thunk URLs to the backend route table.
// Usage: node client-flows.js <clientDir> [<appJsxRelPath>]  ->  JSON
const path = require("path"), fs = require("fs");
const clientDir = process.argv[2] || process.cwd();
const { parser, traverse } = require(path.join(__dirname, "_babel"))(clientDir);

const PLUGINS = ["jsx", "objectRestSpread", "optionalChaining", "nullishCoalescingOperator", "classProperties", "asyncGenerators"];
const parse = (code) => parser.parse(code, { sourceType: "module", plugins: PLUGINS });
const METHODS = new Set(["get", "post", "put", "patch", "delete"]);
const rel = (f) => path.relative(clientDir, f);

// stack knobs (from AUDIT_CONFIG merged profile; ICS/react-redux defaults preserved)
const CFG = (() => { try { return JSON.parse(fs.readFileSync(process.env.AUDIT_CONFIG, "utf8")).knobs || {}; } catch (_) { return {}; } })();
const knob = (k, d) => (CFG[k] === undefined ? d : CFG[k]);
const HTTP_CLIENTS = new Set(knob("http_clients", ["axios", "api", "http"]));
const ACTION_DIRS = knob("action_dirs", ["src/actions"]);
const GUARD_COMPONENT = knob("route_guard_component", "PrivateRoute");
const ROLES_PROP = knob("roles_prop", "allowedRoles");
const COMPONENT_PROP = knob("component_prop", "Component");
const BIND_FN = knob("bind_fn", "connect");
// app entry: argv[3] or knob if that file exists, else autodetect App.jsx/App.js
const _appPref = process.argv[3] || knob("app_entry", null);
const APP = (_appPref && fs.existsSync(path.join(clientDir, _appPref))) ? _appPref
  : (fs.existsSync(path.join(clientDir, "src/components/App.jsx")) ? "src/components/App.jsx" : "src/components/App.js");
const isActionFile = (r) => ACTION_DIRS.some((d) => r.split(path.sep).join("/").includes(d.replace(/^src\//, "").replace(/^\.\//, "") + "/") || r.includes("/" + d.split("/").pop() + "/"));

function listFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
    if (e.name === "node_modules" || e.name.startsWith(".")) continue;
    const f = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...listFiles(f));
    else if (/\.(jsx?|mjs)$/.test(e.name)) out.push(f);
  }
  return out;
}
function resolveImport(fromFile, spec) {
  if (!spec.startsWith(".") && !spec.startsWith("/")) return null;
  const base = spec.startsWith("/") ? path.join(clientDir, spec) : path.resolve(path.dirname(fromFile), spec);
  for (const c of [base, base + ".js", base + ".jsx", base + ".mjs", path.join(base, "index.js"), path.join(base, "index.jsx")]) {
    try { if (fs.statSync(c).isFile()) return c; } catch (_) {}
  }
  return null;
}
function urlOf(node) {
  if (!node) return null;
  if (node.type === "StringLiteral") return node.value;
  if (node.type === "TemplateLiteral") {
    let s = ""; node.quasis.forEach((q, i) => { s += q.value.cooked; if (i < node.expressions.length) s += ":x"; });
    return s;
  }
  return null;
}
// collect axios.<m>(url) / axios({method,url}) within an arbitrary subtree
function axiosCallsIn(node) {
  const calls = [];
  traverse(node, {
    noScope: true,
    CallExpression(p) {
      const c = p.node.callee;
      const ln = p.node.loc ? p.node.loc.start.line : 0;
      if (c && c.type === "MemberExpression" && c.property && METHODS.has((c.property.name || "").toLowerCase())) {
        const on = c.object && (c.object.name || (c.object.callee && c.object.callee.name));
        if (HTTP_CLIENTS.has(on)) {
          const url = urlOf(p.node.arguments[0]);
          if (url) calls.push({ method: c.property.name.toUpperCase(), url, line: ln });
        }
      }
      if (c && c.type === "Identifier" && HTTP_CLIENTS.has(c.name) && p.node.arguments[0] && p.node.arguments[0].type === "ObjectExpression") {
        let method = "GET", url = null;
        for (const pr of p.node.arguments[0].properties) {
          if (!pr.key) continue;
          if (pr.key.name === "method" && pr.value.value) method = String(pr.value.value).toUpperCase();
          if (pr.key.name === "url") url = urlOf(pr.value);
        }
        if (url) calls.push({ method, url, line: ln });
      }
    },
  }, { });
  // dedup
  const m = new Map(); for (const c of calls) m.set(c.method + " " + c.url, c);
  return [...m.values()];
}

const files = listFiles(path.join(clientDir, "src"));
const asts = {};
const imports = {};             // file -> {localName -> resolvedFileRel}
const thunks = [];              // {file, name, calls}
const componentThunks = [];     // {file, component, thunks:[name]}

for (const file of files) {
  let ast; try { ast = parse(fs.readFileSync(file, "utf8")); } catch (_) { continue; }
  asts[file] = ast; const r = rel(file); imports[r] = {};
  traverse(ast, {
    ImportDeclaration(p) {
      const resolved = resolveImport(file, p.node.source.value);
      if (resolved) p.node.specifiers.forEach((s) => { if (s.local) imports[r][s.local.name] = rel(resolved); });
    },
  });
  // THUNKS: exported action-creators in src/actions/** whose body issues axios calls
  if (isActionFile(r)) {
    traverse(ast, {
      ExportNamedDeclaration(p) {
        const d = p.node.declaration; if (!d) return;
        const decls = d.type === "VariableDeclaration" ? d.declarations
          : d.type === "FunctionDeclaration" ? [{ id: d.id, init: d }] : [];
        for (const de of decls) {
          if (!de.id || !de.id.name) continue;
          const calls = axiosCallsIn(de.init || de);
          if (calls.length) thunks.push({ file: r, name: de.id.name, calls });
        }
      },
    });
  }
  // COMPONENT -> THUNK bindings: connect(mapState, { thunkA, thunkB })(Comp)  +  dispatch(thunkName(...))
  traverse(ast, {
    CallExpression(p) {
      const c = p.node.callee;
      // connect(x, { ...thunks })  — 2nd arg object-shorthand or object with values
      if (c && c.type === "Identifier" && c.name === BIND_FN && p.node.arguments[1] && p.node.arguments[1].type === "ObjectExpression") {
        const names = p.node.arguments[1].properties.map((pr) => pr.key && pr.key.name).filter(Boolean);
        // component = the identifier the returned connect(...)(<Comp>) is applied to, else the file
        let comp = null;
        if (p.parent && p.parent.type === "CallExpression" && p.parent.arguments[0] && p.parent.arguments[0].type === "Identifier")
          comp = p.parent.arguments[0].name;
        if (names.length) componentThunks.push({ file: r, component: comp, thunks: names });
      }
      // dispatch(thunkName(args))
      if (c && c.type === "Identifier" && c.name === "dispatch" && p.node.arguments[0] &&
          p.node.arguments[0].type === "CallExpression" && p.node.arguments[0].callee.type === "Identifier") {
        componentThunks.push({ file: r, component: null, thunks: [p.node.arguments[0].callee.name] });
      }
    },
  });
}

// ---- collect string-const exports from a module (for `import * as R from './navigationRoutes'`) ----
function stringConsts(file) {
  const map = {};
  let ast; try { ast = parse(fs.readFileSync(file, "utf8")); } catch (_) { return map; }
  traverse(ast, {
    ExportNamedDeclaration(p) {
      const d = p.node.declaration;
      if (d && d.type === "VariableDeclaration") for (const de of d.declarations)
        if (de.id.name && de.init && de.init.type === "StringLiteral") map[de.id.name] = de.init.value;
    },
  });
  return map;
}

// ---- routes: path <-> PrivateRoute(allowedRoles, Component, flags) in App.jsx ----
const appFile = path.join(clientDir, APP);
const routes = [];
if (fs.existsSync(appFile)) {
  const ast = asts[appFile] || parse(fs.readFileSync(appFile, "utf8"));
  const appImports = imports[rel(appFile)] || {};              // localName -> resolvedFileRel (components)
  // route-path constants: namespace (import * as R) AND named (import { HOME_URL }) from a consts module
  const nsConst = {};        // alias -> {NAME: value}
  const namedConst = {};     // localName -> value
  traverse(ast, {
    ImportDeclaration(p) {
      const f = resolveImport(appFile, p.node.source.value); if (!f) return;
      const consts = stringConsts(f); if (!Object.keys(consts).length) return;
      for (const s of p.node.specifiers) {
        if (s.type === "ImportNamespaceSpecifier") nsConst[s.local.name] = consts;
        else if (s.type === "ImportSpecifier" && consts[s.imported.name] !== undefined) namedConst[s.local.name] = consts[s.imported.name];
      }
    },
  });
  const resolvePath = (node) => {
    if (!node) return undefined;
    if (node.type === "StringLiteral") return node.value;
    if (node.type === "Identifier" && namedConst[node.name] !== undefined) return namedConst[node.name];   // v5: path={HOME_URL}
    if (node.type === "MemberExpression" && node.object.name && nsConst[node.object.name]) {
      const v = nsConst[node.object.name][node.property.name];
      if (v !== undefined) return v;
    }
    return undefined;
  };
  // read roles/component/flags/path off a <PrivateRoute .../> opening element (path prop = v5; absent = v7)
  function guardProps(opening) {
    let roles = [], component = null, pathVal; const flags = [];
    for (const at of opening.attributes) {
      if (at.type !== "JSXAttribute" || !at.name) continue;
      const an = at.name.name, ex = at.value && at.value.expression;
      if (an === ROLES_PROP && ex && ex.type === "ArrayExpression")
        roles = ex.elements.map((e) => e && (e.value ?? (e.property && e.property.name))).filter(Boolean);
      else if ((an === COMPONENT_PROP || an === "component") && ex && ex.type === "Identifier") component = ex.name;
      else if (an === "path") pathVal = at.value && at.value.type === "StringLiteral" ? at.value.value : resolvePath(ex);
      else if (["publicOnly", "decideRole", "requireProfile"].includes(an)) flags.push(an);
    }
    return { roles, component, flags, pathVal };
  }
  function fromPrivateRoute(jsx) {          // v7: the `element:` JSX -> just roles/component/flags
    if (!jsx || jsx.type !== "JSXElement") return null;
    if (jsx.openingElement.name.name !== GUARD_COMPONENT) {
      for (const ch of jsx.children || []) { const r = fromPrivateRoute(ch); if (r) return r; }
      return null;
    }
    return guardProps(jsx.openingElement);
  }
  const appRel = rel(appFile);
  const pushRoute = (pathVal, info, node) => {
    if (pathVal === undefined) return;
    const cf = info.component && appImports[info.component] ? appImports[info.component] : null;
    routes.push({ path: pathVal, roles: info.roles, flags: info.flags, component: info.component, componentFile: cf,
                  loc: appRel + ":" + (node && node.loc ? node.loc.start.line : 0) });
  };
  // v7 data-router: { path, element: <PrivateRoute .../> }
  traverse(ast, {
    ObjectExpression(p) {
      let pathVal, element;
      for (const pr of p.node.properties) {
        if (!pr.key) continue;
        if (pr.key.name === "path") pathVal = resolvePath(pr.value);
        if (pr.key.name === "element") element = pr.value;
      }
      if (pathVal === undefined || !element) return;
      pushRoute(pathVal, fromPrivateRoute(element) || { roles: [], component: null, flags: [] }, p.node);
    },
  });
  // v5 JSX: <PrivateRoute path=... allowedRoles=... component=.../> (path is a prop)
  const seen = new Set(routes.map((r) => r.path));
  traverse(ast, {
    JSXOpeningElement(p) {
      if (p.node.name.name !== GUARD_COMPONENT) return;
      const info = guardProps(p.node);
      if (info.pathVal === undefined || seen.has(info.pathVal)) return;
      seen.add(info.pathVal); pushRoute(info.pathVal, info, p.node);
    },
  });
}

// ---- CLIENT ACTOR MODEL: route(role) -> componentFile -> connect-bound thunks -> axios calls ----
const thunkCalls = {};                          // thunkName -> [{method,url,loc}]  (loc = the axios call site)
for (const t of thunks) (thunkCalls[t.name] ||= []).push(...t.calls.map((c) => ({ method: c.method, url: c.url, loc: t.file + ":" + (c.line || 0) })));
const thunksByFile = {};                        // componentFile -> Set(thunkName)  (connect bindings living in that file)
for (const b of componentThunks) if (b.file) (thunksByFile[b.file] ||= new Set()), b.thunks.forEach((n) => thunksByFile[b.file].add(n));
const frontendActors = routes.map((r) => {
  const bound = r.componentFile ? [...(thunksByFile[r.componentFile] || [])] : [];
  const calls = new Map();
  for (const n of bound) for (const c of thunkCalls[n] || []) calls.set(c.method + " " + c.url, { ...c, thunk: n });
  return { path: r.path, roles: r.roles, flags: r.flags, component: r.component, componentFile: r.componentFile,
           loc: r.loc, thunks: bound, backendCalls: [...calls.values()] };
});

process.stdout.write(JSON.stringify({
  thunks, componentThunks, routes, frontendActors, imports,
  stats: { files: files.length, thunks: thunks.length, thunkCalls: thunks.reduce((n, t) => n + t.calls.length, 0),
           componentThunkBindings: componentThunks.length, routes: routes.length,
           routesWithComponent: routes.filter((r) => r.componentFile).length,
           actorsWithCalls: frontendActors.filter((a) => a.backendCalls.length).length },
}));
