// v2 DB-LAYER extractor (Babel AST) — completes  controller -> query-fn -> Model.operation.
// ICS controllers don't call mongoose directly; they call query fns in database/queries/**.
// Emits:
//   queries     : {fnName: [{model, op, write}]}      each exported query fn -> the DB ops it runs
//   controllers : {file: {calls:[fnName], models:[{model,op,write}]}}   controller -> queries it invokes -> resolved DB touch
// Usage: node db-layer.js <serverDir>  ->  JSON
const path = require("path"), fs = require("fs");
const serverDir = process.argv[2] || process.cwd();
const { parser, traverse } = require(path.join(__dirname, "_babel"))(serverDir);

const CFG = (() => { try { return JSON.parse(fs.readFileSync(process.env.AUDIT_CONFIG, "utf8")).knobs || {}; } catch (_) { return {}; } })();
const knob = (k, d) => (CFG[k] === undefined ? d : CFG[k]);
const CONTROLLER_DIR = knob("controller_dir", "controllers");
const QUERY_DIRS = knob("query_dirs", ["database/queries"]);
const MODEL_DIR = knob("model_dir", "database/models");
const QUERY_RE = new RegExp("(" + QUERY_DIRS.map((d) => d.replace(/[/]/g, "\\/")).join("|") + ")\\/");

const READ = new Set(["find", "findone", "findbyid", "aggregate", "countdocuments", "distinct", "exists", "populate", "findonewithdeleted"]);
const WRITE = new Set(["create", "insertmany", "save", "findbyidandupdate", "findbyidanddelete", "findbyidandremove",
  "findoneandupdate", "findoneanddelete", "findoneandremove", "updateone", "updatemany", "deleteone", "deletemany", "remove", "bulkwrite"]);
const isOp = (n) => READ.has(n.toLowerCase()) || WRITE.has(n.toLowerCase());
const parse = (f) => parser.parse(fs.readFileSync(f, "utf8"), { sourceType: "module", plugins: ["optionalChaining", "objectRestSpread", "nullishCoalescingOperator", "classProperties", "asyncGenerators"] });
function listFiles(dir) {
  const out = []; if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => (a.name < b.name ? -1 : 1))) {
    const f = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...listFiles(f));
    else if (e.name.endsWith(".js")) out.push(f);
  }
  return out;
}
// capture a subtree's DIRECT Model.op(...) db ops + the plain-identifier functions it CALLS (for transitive inlining)
function dbOpsAndCalls(node, modelNames) {
  const ops = [], calls = new Set();
  traverse(node, {
    noScope: true,
    CallExpression(p) {
      let c = p.node.callee;
      while (c && c.type === "MemberExpression" && /^(then|lean|exec|sort|limit|skip|select|populate)$/i.test(c.property.name || "") && c.object.type === "CallExpression")
        c = c.object.callee;
      if (c && c.type === "MemberExpression" && c.property && isOp(c.property.name || "")) {
        let base = c.object; while (base && base.type === "MemberExpression") base = base.object;
        const model = base && base.type === "Identifier" ? base.name : null;
        if (model && (/^[A-Z]/.test(model) || (modelNames && modelNames.has(model))))
          ops.push({ model, op: c.property.name, write: WRITE.has(c.property.name.toLowerCase()), line: p.node.loc ? p.node.loc.start.line : 0 });
      }
      if (p.node.callee.type === "Identifier") calls.add(p.node.callee.name);   // a local-fn call (resolved below)
    },
  }, {});
  return { ops, calls };
}

// ---- pass 1: query fns -> db ops (resolves `module.exports.x = localConst` and inlines local-fn calls) ----
const queries = {};   // fnName -> [{model,op,write,loc}]
const qFiles = QUERY_DIRS.flatMap((d) => listFiles(path.join(serverDir, d)));
const modelNames = new Set();
for (const f of listFiles(path.join(serverDir, MODEL_DIR))) modelNames.add(path.basename(f, ".js"));
for (const f of qFiles) {
  let ast; try { ast = parse(f); } catch (_) { continue; }
  const rel = path.relative(serverDir, f);
  // local function definitions in this file (so `module.exports.x = updateAttendeesList` resolves to the fn body)
  const localFns = {};
  traverse(ast, {
    VariableDeclarator(p) { if (p.node.id.type === "Identifier" && p.node.init && /^(ArrowFunctionExpression|FunctionExpression)$/.test(p.node.init.type)) localFns[p.node.id.name] = p.node.init; },
    FunctionDeclaration(p) { if (p.node.id) localFns[p.node.id.name] = p.node; },
  });
  // ops of a fn + transitively the ops of any LOCAL fn it calls (cycle-guarded, within the file)
  function resolveOps(fnNode, seen) {
    const { ops, calls } = dbOpsAndCalls(fnNode, modelNames);
    const all = [...ops];
    for (const cn of calls) if (localFns[cn] && !seen.has(cn)) { seen.add(cn); all.push(...resolveOps(localFns[cn], seen)); }
    return all;
  }
  const stamp = (ops) => { const m = new Map(); for (const o of ops) m.set(o.model + "." + o.op, o); return [...m.values()].map((o) => ({ model: o.model, op: o.op, write: o.write, loc: rel + ":" + (o.line || 0) })); };
  const exportFn = (name, valNode) => {
    const fnNode = valNode.type === "Identifier" && localFns[valNode.name] ? localFns[valNode.name] : valNode;   // resolve var-ref exports
    const ops = resolveOps(fnNode, new Set());
    if (ops.length) queries[name] = stamp(ops);
  };
  traverse(ast, {
    AssignmentExpression(p) {   // module.exports.NAME = <fn | localIdent>
      const l = p.node.left;
      if (l.type === "MemberExpression" && l.object.type === "MemberExpression" &&
          l.object.object.name === "module" && l.object.property.name === "exports" && l.property.name) exportFn(l.property.name, p.node.right);
    },
    ExpressionStatement(p) {   // module.exports = { NAME: fn|localIdent, ... }
      const e = p.node.expression;
      if (e && e.type === "AssignmentExpression" && e.left.type === "MemberExpression" &&
          e.left.object.name === "module" && e.left.property.name === "exports" && e.right.type === "ObjectExpression")
        for (const pr of e.right.properties) if (pr.key && pr.value) exportFn(pr.key.name, pr.value);
    },
  });
}

// ---- pass 2: controller -> query fns it imports & calls -> resolved db ops ----
const controllers = {};
for (const f of listFiles(path.join(serverDir, CONTROLLER_DIR))) {
  let ast; try { ast = parse(f); } catch (_) { continue; }
  const imported = new Set();  // names imported from the query wrapper layer
  const called = new Set();
  traverse(ast, {
    VariableDeclarator(p) {
      const init = p.node.init;
      if (init && init.type === "CallExpression" && init.callee.name === "require" && init.arguments[0] &&
          QUERY_RE.test(init.arguments[0].value || "")) {
        if (p.node.id.type === "Identifier") imported.add(p.node.id.name);
        else if (p.node.id.type === "ObjectPattern") p.node.id.properties.forEach((pr) => pr.key && imported.add((pr.value && pr.value.name) || pr.key.name));
      }
    },
    CallExpression(p) { const c = p.node.callee; if (c && c.type === "Identifier") called.add(c.name); },
  });
  const calls = [...imported].filter((n) => called.has(n) && queries[n]);
  // also fall back to any imported query fn even if the call ident wasn't matched (aliased calls)
  const use = calls.length ? calls : [...imported].filter((n) => queries[n]);
  if (use.length) {
    const models = new Map();
    for (const fn of use) for (const o of queries[fn]) models.set(o.model + "." + o.op, o);
    controllers[path.relative(serverDir, f)] = { calls: use, models: [...models.values()] };
  }
}

process.stdout.write(JSON.stringify({
  queries, controllers,
  stats: { queryFns: Object.keys(queries).length, controllersWithDb: Object.keys(controllers).length, models: modelNames.size },
}));
