const User = require('../models/User');
module.exports.updateUserById = (id, data) => User.findByIdAndUpdate(id, data);
