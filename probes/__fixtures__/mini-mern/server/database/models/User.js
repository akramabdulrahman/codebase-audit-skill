const mongoose = require('mongoose');
const schema = new mongoose.Schema({ email: String, org: { type: mongoose.Schema.Types.ObjectId, ref: 'Org' }, role: String });
module.exports = mongoose.model('User', schema);
