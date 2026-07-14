// v2 extractor probe — ORM entity-ownership graph (Mongoose).
// Primary: reflect the REAL schema (require mongoose + the models — the framework is the oracle).
// Fallback: if mongoose isn't installed, AST-parse the model files for `new Schema({... ref ...})` + model name.
// Usage: node mongoose-schema.js <serverDir>   Emits {owner_links, model_names, errors, source:reflect|ast}.
const path = require('path');
const fs = require('fs');

const serverDir = process.argv[2] || process.cwd();
const CFG = (() => { try { return JSON.parse(fs.readFileSync(process.env.AUDIT_CONFIG, 'utf8')).knobs || {}; } catch (_) { return {}; } })();
const MODEL_DIR = (CFG.model_dir === undefined ? 'database/models' : CFG.model_dir);
const modelsDir = path.join(serverDir, MODEL_DIR);
const emit = (o) => process.stdout.write(JSON.stringify(o));

if (!fs.existsSync(modelsDir)) {
  emit({ owner_links: [], model_names: [], errors: [], source: 'none',
         facet_error: 'model dir not found: ' + MODEL_DIR + ' — set knobs.model_dir, or this stack has no models/ dir' });
  process.exit(0);
}
const modelFiles = fs.readdirSync(modelsDir).sort().filter((f) => f.endsWith('.js') || f.endsWith('.ts'));

// ---------- primary: reflect real mongoose ----------
let mongoose = null;
try { mongoose = require(path.join(serverDir, 'node_modules', 'mongoose')); } catch (_) { mongoose = null; }
if (mongoose) {
  const errors = [];
  for (const f of modelFiles) { try { require(path.join(modelsDir, f)); } catch (e) { errors.push({ file: f, error: String(e.message || e).slice(0, 120) }); } }
  const out = [];
  for (const name of mongoose.modelNames()) {
    mongoose.model(name).schema.eachPath((p, type) => {
      const inst = type.instance;
      let ref = (type.options && type.options.ref) || null;
      let isArray = inst === 'Array' || (type.$isMongooseArray === true);
      if (!ref && type.caster && type.caster.options && type.caster.options.ref) { ref = type.caster.options.ref; isArray = true; }
      if (ref) out.push({ model: name, field: p, instance: inst, ref, isArray: !!isArray });
    });
  }
  if (mongoose.modelNames().length) { emit({ owner_links: out, model_names: mongoose.modelNames(), errors, source: 'reflect' }); process.exit(0); }
  // mongoose present but no models registered (odd) -> fall through to AST
}

// ---------- fallback: AST-parse the model source ----------
const { parser, traverse } = require(path.join(__dirname, '_babel'))(serverDir);
const owner_links = [];
const model_names = new Set();
const errors = [];
// pull `ref: 'X'` (and array-of-ref) out of a Schema definition object
function refsFromSchemaObj(objNode, modelName) {
  for (const pr of objNode.properties || []) {
    if (!pr.key) continue;
    const field = pr.key.name || pr.key.value;
    let v = pr.value, isArray = false;
    if (v.type === 'ArrayExpression' && v.elements[0]) { v = v.elements[0]; isArray = true; }   // field: [ {..ref..} ]
    if (v.type !== 'ObjectExpression') continue;
    let ref = null, typeIsArray = false;
    for (const p2 of v.properties) {
      if (!p2.key) continue;
      if ((p2.key.name || p2.key.value) === 'ref' && (p2.value.value)) ref = p2.value.value;
      if ((p2.key.name || p2.key.value) === 'type' && p2.value.type === 'ArrayExpression') typeIsArray = true; // type:[ObjectId]
    }
    if (ref) owner_links.push({ model: modelName, field, instance: (isArray || typeIsArray) ? 'Array' : 'ObjectID', ref, isArray: isArray || typeIsArray });
  }
}
for (const f of modelFiles) {
  let ast; try { ast = parser.parse(fs.readFileSync(path.join(modelsDir, f), 'utf8'), { sourceType: 'module', plugins: ['typescript', 'objectRestSpread', 'optionalChaining', 'classProperties'] }); }
  catch (e) { errors.push({ file: f, error: String(e.message || e).slice(0, 120) }); continue; }
  // map local schema var -> its ObjectExpression, and model('Name', schemaVar)
  const schemaObjs = {};   // varName -> ObjectExpression
  let inlineName = null;
  traverse(ast, {
    NewExpression(p) {   // const s = new (mongoose.)Schema({...})
      const c = p.node.callee;
      const isSchema = (c.type === 'Identifier' && c.name === 'Schema') || (c.type === 'MemberExpression' && c.property.name === 'Schema');
      if (!isSchema || !p.node.arguments[0] || p.node.arguments[0].type !== 'ObjectExpression') return;
      const decl = p.findParent((x) => x.isVariableDeclarator());
      if (decl && decl.node.id.name) schemaObjs[decl.node.id.name] = p.node.arguments[0];
      else schemaObjs['__inline__'] = p.node.arguments[0];   // model('N', new Schema({...}))
    },
    CallExpression(p) {  // mongoose.model('Name', schema)  /  model('Name', new Schema({...}))
      const c = p.node.callee;
      const isModel = (c.type === 'MemberExpression' && c.property.name === 'model') || (c.type === 'Identifier' && c.name === 'model');
      if (!isModel) return;
      const a = p.node.arguments;
      if (!a[0] || a[0].type !== 'StringLiteral') return;
      const name = a[0].value; model_names.add(name);
      let obj = null;
      if (a[1] && a[1].type === 'Identifier' && schemaObjs[a[1].name]) obj = schemaObjs[a[1].name];
      else if (a[1] && a[1].type === 'NewExpression' && schemaObjs['__inline__']) obj = schemaObjs['__inline__'];
      if (obj) refsFromSchemaObj(obj, name);
    },
  });
}
emit({ owner_links, model_names: [...model_names], errors, source: 'ast',
       facet_error: mongoose ? null : 'mongoose not installed — used AST fallback (less precise than schema reflection)' });
