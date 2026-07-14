const { deleteWidgetById } = require('../../database/queries/widget');
module.exports = async (req, res) => res.json(await deleteWidgetById(req.params.id));  // NO ownership check
