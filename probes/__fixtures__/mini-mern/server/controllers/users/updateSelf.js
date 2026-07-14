const { updateUserById } = require('../../database/queries/user');
module.exports = async (req, res) => res.json(await updateUserById(req.user._id, req.body)); // self-service
