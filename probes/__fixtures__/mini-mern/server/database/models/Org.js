const mongoose = require('mongoose');
module.exports = mongoose.model('Org', new mongoose.Schema({ name: String }));
