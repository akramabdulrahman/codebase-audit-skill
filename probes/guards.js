// v2 accurate enforcement extractor (Babel AST). Per controller file, alias-aware:
//   - resolves `const x = req.user` aliases + `const {tpOrganisation} = req.user` destructuring
//   - finds ownership GUARDS (tenant attr compared, inside a forbidding conditional)
//   - captures the COMPARED RESOURCE FIELD (disambiguates the predicate: runByTrainingProvider vs tpOrganisation)
// Usage: node guards.js <serverDir>  ->  JSON [{file, hasOwnershipGuard, comparedFields:[{field,attr}], sinks[]}]
const path = require("path"), fs = require("fs");
const serverDir = process.argv[2] || process.cwd();
const { parser, traverse } = require(path.join(__dirname, "_babel"))(serverDir);

// knobs (this probe previously ignored config — the ICS-hardcoding landmine the skeptics found).
// Defaults = the ICS values, so ICS output is unchanged; other stacks set tenant_attrs / controller_dir / user_root.
const CFG = (() => { try { return JSON.parse(fs.readFileSync(process.env.AUDIT_CONFIG, "utf8")).knobs || {}; } catch (_) { return {}; } })();
const knob = (k, d) => (CFG[k] === undefined ? d : CFG[k]);
const CONTROLLER_DIR = knob("controller_dir", "controllers");
const ATTRS = knob("tenant_attrs", ["tpOrganisation", "employerOrganisation", "programmesCanDeliver", "_id", "userId", "credentialId"]); // NOT role (that's the discriminator, not an owner link)
const USER_ROOT = knob("user_root", "req.user");   // e.g. "req.currentUser", "ctx.state.user"
const [UROOT_OBJ, UROOT_PROP] = USER_ROOT.includes(".") ? USER_ROOT.split(".") : [USER_ROOT, null];
const unwrap = (n) => {
  // strip .toString()/.valueOf()/String(x) and ||-default wrappers to reach the real expression
  if (!n) return n;
  if (n.type === "CallExpression" && n.callee && n.callee.type === "MemberExpression" && /^(toString|valueOf)$/.test(n.callee.property.name)) return unwrap(n.callee.object);
  if (n.type === "CallExpression" && n.callee && n.callee.name === "String" && n.arguments[0]) return unwrap(n.arguments[0]);
  if (n.type === "LogicalExpression") return unwrap(n.left);
  return n;
};
const SINK = /^(findByIdAndUpdate|findByIdAndDelete|findByIdAndRemove|deleteOne|deleteMany|updateOne|updateMany|findOneAndUpdate|findOneAndDelete)$/;
const FORBID = /forbidden|unauthorized|badData|Access denied/;

function listFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
    const f = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...listFiles(f));
    else if (e.name.endsWith(".js")) out.push(f);
  }
  return out;
}
const isRootExpr = (n) => UROOT_PROP
  ? (n && n.type === "MemberExpression" && n.object && n.object.name === UROOT_OBJ && n.property && n.property.name === UROOT_PROP)
  : (n && n.type === "Identifier" && n.name === UROOT_OBJ);
const isUserRoot = (n, aliases) => isRootExpr(n) || (n && n.type === "Identifier" && aliases.has(n.name));

// --- role-conditional guard coverage: which roles a guard actually covers (fixes the role-branch FN) ---
const ROLE_ATTR = knob("role_attr", "role");
const isRoleRef = (n, aliases) => n && n.type === "MemberExpression" && n.property && n.property.name === ROLE_ATTR && isUserRoot(n.object, aliases);
const roleValOf = (n) => n ? (n.type === "MemberExpression" && n.property ? n.property.name : (n.type === "StringLiteral" ? n.value : null)) : null;
function testRoles(t, aliases) {   // if an `if` test gates by caller role, return [roleValues]; else null
  if (!t) return null;
  if (t.type === "LogicalExpression") { const u = [...(testRoles(t.left, aliases) || []), ...(testRoles(t.right, aliases) || [])]; return u.length ? u : null; }
  if (t.type === "BinaryExpression" && (t.operator === "===" || t.operator === "==")) {
    if (isRoleRef(t.left, aliases)) { const v = roleValOf(t.right); return v ? [v] : null; }
    if (isRoleRef(t.right, aliases)) { const v = roleValOf(t.left); return v ? [v] : null; }
  }
  if (t.type === "CallExpression" && t.callee.type === "MemberExpression" && t.callee.property.name === "includes" &&
      t.callee.object.type === "ArrayExpression" && isRoleRef(t.arguments[0], aliases))
    return t.callee.object.elements.map(roleValOf).filter(Boolean);
  return null;
}
function guardingRoles(p, aliases) {   // roles gating the branch this node sits in (via `if (role===X) {...}`)
  const roles = new Set(); let cur = p;
  for (let i = 0; i < 8 && cur; i++) {
    const ifp = cur.findParent((x) => x.isIfStatement());
    if (!ifp) break;
    const rs = testRoles(ifp.node.test, aliases);
    if (rs) rs.forEach((r) => roles.add(r));   // assume the check is in the consequent (the common ownership-branch shape)
    cur = ifp;
  }
  return roles;
}

const ctrlDir = path.join(serverDir, CONTROLLER_DIR);
if (!fs.existsSync(ctrlDir)) {   // crash-guard: keep the array contract; warn on stderr (proper envelope = P0-a coverage pass)
  process.stderr.write("[guards] controller dir not found: " + CONTROLLER_DIR + " — set knobs.controller_dir; emitting []\n");
  process.stdout.write("[]"); process.exit(0);
}
const results = [];
for (const file of listFiles(ctrlDir)) {
  let ast;
  try {
    ast = parser.parse(fs.readFileSync(file, "utf8"), {
      sourceType: "module",
      plugins: ["optionalChaining", "nullishCoalescingOperator", "objectRestSpread", "asyncGenerators", "classProperties"],
    });
  } catch (e) { continue; }
  const aliases = new Set();        // vars that alias req.user (incl. `req.user || {}`)
  const destructured = new Set();   // tenant attrs destructured off req.user
  const tenantVars = {};            // local var -> tenant attr it carries (e.g. employerOrgId -> employerOrganisation)
  // pass 1: resolve aliases + destructures (unwrap ||-default)
  traverse(ast, {
    VariableDeclarator(p) {
      const { id } = p.node; const init = unwrap(p.node.init);
      const initIsUser = init && init.type === "MemberExpression" && init.object.name === "req" && init.property.name === "user";
      const initIsAlias = init && init.type === "Identifier" && aliases.has(init.name);
      if (initIsUser || initIsAlias) {
        if (id.type === "Identifier") aliases.add(id.name);
        if (id.type === "ObjectPattern") id.properties.forEach((pr) => pr.key && destructured.add(pr.key.name));
      }
    },
  });
  const attrRef = (node) => {
    node = unwrap(node);
    if (node && node.type === "MemberExpression" && isUserRoot(node.object, aliases) && ATTRS.includes(node.property.name)) return node.property.name;
    if (node && node.type === "Identifier" && destructured.has(node.name) && ATTRS.includes(node.name)) return node.name;
    if (node && node.type === "Identifier" && tenantVars[node.name]) return tenantVars[node.name];
    return null;
  };
  const otherField = (node) => {
    node = unwrap(node);
    if (node && node.type === "MemberExpression" && node.property && node.property.name && !isUserRoot(node.object, aliases)) return node.property.name;
    return null;
  };
  // pass 1b: propagate tenant attrs into local vars (`const employerOrgId = currentUser.employerOrganisation`)
  traverse(ast, {
    VariableDeclarator(p) {
      if (p.node.id.type !== "Identifier") return;
      const a = attrRef(p.node.init);
      if (a) tenantVars[p.node.id.name] = a;
    },
  });
  const compared = [];
  let hasGuard = false, hasSink = false, sinkLine = 0;
  let coverAll = false; const coverRoles = new Set();   // role-conditional coverage of the ownership guard(s)
  const rel = path.relative(serverDir, file);
  const at = (node) => rel + ":" + (node.loc ? node.loc.start.line : 0);
  const cover = (p) => { const gr = guardingRoles(p, aliases); if (gr.size === 0) coverAll = true; else gr.forEach((r) => coverRoles.add(r)); };
  const comparisons = [], sinkOffsets = [];             // for DOMINANCE: does a comparison actually gate a sink?
  const recordCmp = (p) => {                            // capture the comparison's offset + the var it's assigned to (if any)
    // nearest enclosing assignment/declarator, skipping callback functions (ownership set as `isAllowed = arr.some(x => cmp)`)
    const anc = p.find((x) => x.isVariableDeclarator() || x.isAssignmentExpression());
    let varName = null;
    if (anc && anc.isVariableDeclarator() && anc.node.id.type === "Identifier") varName = anc.node.id.name;        // const V = cmp
    else if (anc && anc.isAssignmentExpression() && anc.node.left.type === "Identifier") varName = anc.node.left.name; // V = cmp
    comparisons.push({ start: p.node.start, end: p.node.end, varName });
  };
  // pass 2: guards + sinks
  traverse(ast, {
    BinaryExpression(p) {
      if (!["===", "!==", "==", "!="].includes(p.node.operator)) return;
      const a = attrRef(p.node.left) || attrRef(p.node.right);
      if (!a) return;
      const f = otherField(p.node.left) || otherField(p.node.right);
      hasGuard = true; cover(p); recordCmp(p);
      if (f) compared.push({ field: f, attr: a, loc: at(p.node) });
    },
    CallExpression(p) {
      const callee = p.node.callee;
      // membership guards: X.some/find/filter/every/includes(...) where the body compares a tenant attr
      if (callee && callee.type === "MemberExpression" && /^(some|find|filter|every|includes)$/.test(callee.property.name)) {
        let mem = false;
        const arrAttr = attrRef(callee.object);
        if (arrAttr) { mem = true; compared.push({ field: otherField(callee.object) || "*", attr: arrAttr, loc: at(p.node) }); }
        p.traverse({ BinaryExpression(q) {
          const a = attrRef(q.node.left) || attrRef(q.node.right);
          if (a) { mem = true; compared.push({ field: otherField(q.node.left) || otherField(q.node.right) || "*", attr: a, loc: at(q.node) }); }
        } });
        const argAttr = p.node.arguments[0] && attrRef(p.node.arguments[0]);
        if (argAttr) { mem = true; compared.push({ field: otherField(callee.object) || "*", attr: argAttr, loc: at(p.node) }); }
        if (mem) { hasGuard = true; cover(p); recordCmp(p); }   // record the .some/.find call itself (assigned to `member`)
      }
      // sinks: mongoose mutators
      if (callee && callee.type === "MemberExpression" && SINK.test(callee.property.name)) { hasSink = true; sinkOffsets.push(p.node.start); if (!sinkLine) sinkLine = p.node.loc ? p.node.loc.start.line : 0; }
    },
  });
  const src = fs.readFileSync(file, "utf8");
  // DOMINANCE: a comparison is a real ownership guard only if it gates an `if` whose FAIL branch forbids/returns,
  // AND a sink exists OUTSIDE that forbidding branch (mutation runs only when the check passes). Kills the file-wide-FORBID FP.
  const forbidIfs = [];
  traverse(ast, {
    IfStatement(p) {
      const branchForbids = (b) => { if (!b) return null; const s = src.slice(b.start, b.end); return (FORBID.test(s) && /\b(return|throw|next)\b/.test(s)) ? [b.start, b.end] : null; };
      const fb = branchForbids(p.node.consequent) || branchForbids(p.node.alternate);
      if (!fb) return;
      const testVars = new Set();
      p.get("test").traverse({ Identifier(q) { testVars.add(q.node.name); } });
      if (p.node.test.type === "Identifier") testVars.add(p.node.test.name);
      forbidIfs.push({ testStart: p.node.test.start, testEnd: p.node.test.end, forbidStart: fb[0], forbidEnd: fb[1], testVars });
    },
  });
  // the tenant comparison must BE the test of (or feed a var tested by) an `if` whose fail-branch forbids/returns.
  // (controllers call query WRAPPERS, not mongoose sinks directly, so we can't require a sink here — the gating `if` is the signal.)
  const cmpGates = (c) => forbidIfs.some((f) =>
    (c.start >= f.testStart && c.end <= f.testEnd) || (c.varName && f.testVars.has(c.varName)));
  const ownershipGuard = comparisons.some(cmpGates);
  // self-identity signal: does the handler act on the CALLER's own identity (self-service, not IDOR)?
  const selfId = /req\.user\.(_id|userId|credentialId|email)\b/.test(src) ||
    ["_id", "userId", "credentialId"].some((a) => destructured.has(a));
  results.push({
    file: path.relative(serverDir, file),
    hasOwnershipGuard: ownershipGuard,
    // which roles the guard covers: null = unconditional (covers all); [roles] = only these (role-conditional branch)
    guardRoles: ownershipGuard ? (coverAll ? null : [...coverRoles].sort()) : null,
    comparedFields: Array.from(new Map(compared.map((c) => [c.field + ":" + c.attr, c])).values()),
    hasSink, sinkLoc: hasSink ? rel + ":" + sinkLine : null, selfId,
    aliases: Array.from(aliases),
    destructured: Array.from(destructured),
  });
}
process.stdout.write(JSON.stringify(results));
