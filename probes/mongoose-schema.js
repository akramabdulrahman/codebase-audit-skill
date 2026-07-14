// v2 extractor probe — ORM entity-ownership graph (Mongoose).
// Usage: cd <serverDir> && node <abs>/mongoose-schema.js <serverDir>
// Emits JSON [{model, field, instance, ref, isArray}] on stdout. Pure schema walk — no DB connection.
const path = require('path');
const fs = require('fs');

const serverDir = process.argv[2] || process.cwd();
const mongoose = require(path.join(serverDir, 'node_modules', 'mongoose'));
const modelsDir = path.join(serverDir, 'database', 'models');

const errors = [];
for (const f of fs.readdirSync(modelsDir)) {
  if (!f.endsWith('.js')) continue;
  try {
    require(path.join(modelsDir, f));
  } catch (e) {
    errors.push({ file: f, error: String(e.message || e).slice(0, 120) });
  }
}

const out = [];
for (const name of mongoose.modelNames()) {
  const schema = mongoose.model(name).schema;
  schema.eachPath((p, type) => {
    const inst = type.instance; // ObjectID, Array, String, ...
    // direct ref: { type: ObjectId, ref } ; array-of-ref: caster carries ref ; non-standard: options.ref on an array path
    let ref = (type.options && type.options.ref) || null;
    let isArray = inst === 'Array' || (type.$isMongooseArray === true);
    if (!ref && type.caster && type.caster.options && type.caster.options.ref) {
      ref = type.caster.options.ref;
      isArray = true;
    }
    if (ref) out.push({ model: name, field: p, instance: inst, ref, isArray: !!isArray });
  });
}

process.stdout.write(JSON.stringify({ owner_links: out, model_names: mongoose.modelNames(), errors }));
