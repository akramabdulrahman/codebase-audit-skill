const mongoose = require('mongoose');
const schema = new mongoose.Schema({ name: String, org: { type: mongoose.Schema.Types.ObjectId, ref: 'Org' } });
module.exports = mongoose.model('Widget', schema);
