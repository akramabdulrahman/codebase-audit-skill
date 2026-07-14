// v2 BACKEND ACTOR MODEL (Babel AST) — the AUTHORITATIVE role->route->controller map, full coverage.
// Walks the mount tree from router/index.js (direct routes + every `router.use('/prefix', subRouter)`),
// so sub-router endpoints are captured with their FULL path (full mount-tree coverage).
// Per route resolves the authorization from the `authentication({...})` middleware:
//   allowedRoles:[userRoles.x]  -> roles (resolved via the constants module, not regex)
//   allowPublic:true            -> public
//   authentication()            -> authed-any   (any logged-in user; roles=[], authAny=true)
//   (no authentication arg)     -> open         (no auth middleware at all)
// Usage: node backend-actors.js <serverDir>  ->  JSON {routes:[{method,path,roles,auth,controller,router}], role_consts}
const path = require("path"), fs = require("fs");
const serverDir = process.argv[2] || process.cwd();
const { parser, traverse } = require(path.join(__dirname, "_babel"))(serverDir);

// stack knobs (from AUDIT_CONFIG merged profile; ICS defaults preserved) — see stacks/capabilities.yaml
const CFG = (() => { try { return JSON.parse(fs.readFileSync(process.env.AUDIT_CONFIG, "utf8")).knobs || {}; } catch (_) { return {}; } })();
const knob = (k, d) => (CFG[k] === undefined ? d : CFG[k]);
const ROUTER_ENTRY = knob("router_entry", "router/index.js");
const API_PREFIX = knob("api_prefix", "/api");
const AUTH_FN = knob("auth_fn", "authentication");
const ROLES_KEY = knob("roles_key", "allowedRoles");
const PUBLIC_KEY = knob("public_key", "allowPublic");
const SPECIAL_FLAGS = knob("special_flags", ["decideRoleFlow"]);
const ROLE_CONSTS_MODULE = knob("role_consts_module", "constants");

// resolve role constants (userRoles.admin -> "admin")
let CONST = {};
for (const c of [ROLE_CONSTS_MODULE, path.join(ROLE_CONSTS_MODULE, "index.js")]) { try { CONST = require(path.join(serverDir, c)); break; } catch (_) {} }
const roleVal = (n) => {
  if (!n) return null;
  if (n.type === "MemberExpression") { const v = CONST[n.object.name] && CONST[n.object.name][n.property.name]; return v || n.property.name; }
  if (n.type === "StringLiteral") return n.value;
  if (n.type === "Identifier") return n.name;
  return null;
};
const METHODS = new Set(["get", "post", "put", "patch", "delete"]);
const parse = (f) => parser.parse(fs.readFileSync(f, "utf8"), { sourceType: "module", plugins: ["typescript", "optionalChaining", "objectRestSpread", "nullishCoalescingOperator", "classProperties"] });

// in-controller RBAC: does the handler body itself gate by role? (the case route-level allowedRoles misses)
// Detects `<user>.role === X` / `!== X` and `[..].includes(<user>.role)`, resolving role constants.
const USER_ROOT = knob("user_root", "req.user");
const [UROOT_OBJ, UROOT_PROP] = USER_ROOT.includes(".") ? USER_ROOT.split(".") : [USER_ROOT, null];
const ROLE_ATTR = knob("role_attr", "role");
const isUserRootN = (n, aliases) =>
  (UROOT_PROP ? (n && n.type === "MemberExpression" && n.object && n.object.name === UROOT_OBJ && n.property && n.property.name === UROOT_PROP)
              : (n && n.type === "Identifier" && n.name === UROOT_OBJ)) ||
  (n && n.type === "Identifier" && aliases && aliases.has(n.name));
const isRoleExprA = (n, aliases) => n && n.type === "MemberExpression" && n.property && n.property.name === ROLE_ATTR && isUserRootN(n.object, aliases);
const gateCache = {};
function controllerRoleGate(absFile) {
  if (absFile in gateCache) return gateCache[absFile];
  let ast; try { ast = parse(absFile); } catch (_) { return (gateCache[absFile] = { gated: false, roles: [] }); }
  const roles = new Set(); let gated = false;
  // resolve `const currentUser = req.user` aliases so `currentUser.role === X` is seen (guards.js does this too)
  const aliases = new Set();
  traverse(ast, {
    VariableDeclarator(p) {
      const init = p.node.init;
      if (p.node.id.type === "Identifier" && init && init.type === "MemberExpression" &&
          UROOT_PROP && init.object && init.object.name === UROOT_OBJ && init.property && init.property.name === UROOT_PROP) aliases.add(p.node.id.name);
    },
  });
  const isRoleExpr = (n) => isRoleExprA(n, aliases);
  traverse(ast, {
    BinaryExpression(p) {
      if (!/^[=!]==?$/.test(p.node.operator)) return;
      if (isRoleExpr(p.node.left) || isRoleExpr(p.node.right)) {
        gated = true;
        const other = isRoleExpr(p.node.left) ? p.node.right : p.node.left;
        const v = roleVal(other); if (v) roles.add(v);
      }
    },
    CallExpression(p) {   // [roleA, roleB].includes(user.role)
      const c = p.node.callee;
      if (c && c.type === "MemberExpression" && c.property.name === "includes" && p.node.arguments[0] && isRoleExpr(p.node.arguments[0])) {
        gated = true;
        if (c.object.type === "ArrayExpression") c.object.elements.forEach((e) => { const v = roleVal(e); if (v) roles.add(v); });
      }
    },
  });
  return (gateCache[absFile] = { gated, roles: [...roles] });
}

// collect `const X = require('rel')` (+ destructured) import map for one file
function importsOf(ast) {
  const imp = {};
  traverse(ast, {
    VariableDeclarator(p) {
      const init = p.node.init;
      if (init && init.type === "CallExpression" && init.callee.name === "require" && init.arguments[0] && init.arguments[0].value) {
        const src = init.arguments[0].value;
        if (p.node.id.type === "Identifier") imp[p.node.id.name] = src;
        else if (p.node.id.type === "ObjectPattern") p.node.id.properties.forEach((pr) => pr.key && (imp[(pr.value && pr.value.name) || pr.key.name] = src));
      }
    },
  });
  return imp;
}
function resolveFile(fromFile, spec) {
  const base = path.resolve(path.dirname(fromFile), spec);
  for (const c of [base, base + ".js", path.join(base, "index.js")]) { try { if (fs.statSync(c).isFile()) return c; } catch (_) {} }
  return null;
}

// parse ONE router file at a given URL prefix -> push routes; return mounts it declares
function parseRouter(file, prefix, out) {
  let ast; try { ast = parse(file); } catch (_) { return []; }
  const imp = importsOf(ast);
  const mounts = []; // {prefix, file}
  traverse(ast, {
    CallExpression(p) {
      const c = p.node.callee;
      if (!(c && c.type === "MemberExpression")) return;
      const m = (c.property.name || "").toLowerCase();
      // router.use('/prefix', SubRouter)  /  router.use(SubRouter)  -> a mount
      if (m === "use") {
        const a = p.node.arguments;
        let subPrefix = "", nameNode = a[0];
        if (a[0] && a[0].type === "StringLiteral") { subPrefix = a[0].value; nameNode = a[1]; }
        if (nameNode && nameNode.type === "Identifier" && imp[nameNode.name]) {
          const rf = resolveFile(file, imp[nameNode.name]);
          if (rf) mounts.push({ prefix: prefix + subPrefix, file: rf });
        }
        return;
      }
      if (!METHODS.has(m)) return;
      const args = p.node.arguments;
      if (!args.length || !(args[0].type === "StringLiteral" || args[0].type === "TemplateLiteral")) return;
      const sub = args[0].type === "StringLiteral" ? args[0].value : args[0].quasis.map((q) => q.value.cooked).join(":x");
      let roles = [], auth = "open", controller = null; // open = no auth middleware
      for (const a of args.slice(1)) {
        if (a.type === "CallExpression" && a.callee.name === AUTH_FN) {
          auth = "authed-any"; const opt = a.arguments[0];
          if (opt && opt.type === "ObjectExpression") {
            for (const pr of opt.properties) {
              if (!pr.key) continue;
              if (pr.key.name === ROLES_KEY) {
                // array literal:  allowedRoles: [userRoles.admin, ...]
                if (pr.value.type === "ArrayExpression") { roles = pr.value.elements.map(roleVal).filter(Boolean); auth = "roles"; }
                // constant identifier:  allowedRoles: sessionManagementRoles  (a role-set const) -> resolve it
                else if (pr.value.type === "Identifier") { const v = CONST[pr.value.name]; if (Array.isArray(v)) { roles = v.slice(); auth = "roles"; } }
              }
              if (pr.key.name === PUBLIC_KEY && pr.value.value === true) auth = "public";
              if (SPECIAL_FLAGS.includes(pr.key.name) && pr.value.value === true) auth = "decide-role";
            }
          }
        } else if (a.type === "Identifier") controller = imp[a.name] || a.name;
      }
      // resolve the controller require to a SERVER-RELATIVE file path (so it joins to guards/db-layer keys,
      // which use path.relative(serverDir,file)); keep the sub-router's directory instead of the bare name
      let ctrlOut = controller, gate = { gated: false, roles: [] };
      if (controller) {
        const rf = resolveFile(file, controller.startsWith(".") ? controller : "./" + controller);
        ctrlOut = rf ? path.relative(serverDir, rf) : controller.replace(/^\.\//, "");
        if (rf) gate = controllerRoleGate(rf);
      }
      // if the route has no route-level role gate but the controller gates by role, record that (kills false "client-side-only-authz")
      const controllerGated = (auth === "authed-any" || auth === "open") && gate.gated;
      const line = p.node.loc ? p.node.loc.start.line : 0;
      out.push({ method: m.toUpperCase(), path: (prefix + sub).replace(/\/+/g, "/"), roles, auth,
                 controllerGated, controllerRoles: gate.roles,
                 controller: ctrlOut, router: path.relative(serverDir, file),
                 loc: path.relative(serverDir, file) + ":" + line });
    },
  });
  return mounts;
}

const out = [];
const seen = new Set();
const queue = [{ file: path.join(serverDir, ROUTER_ENTRY), prefix: API_PREFIX }];
while (queue.length) {
  const { file, prefix } = queue.shift();
  if (seen.has(file + "@" + prefix)) continue; seen.add(file + "@" + prefix);
  for (const mnt of parseRouter(file, prefix, out)) queue.push(mnt);
}
// dedup routes
const uniq = new Map();
for (const r of out) uniq.set(r.method + " " + r.path, r);
process.stdout.write(JSON.stringify({
  routes: [...uniq.values()],
  role_consts: Object.keys(CONST).filter((k) => Array.isArray(CONST[k]) || (CONST[k] && typeof CONST[k] === "object")),
  stats: { total: uniq.size },
}));
