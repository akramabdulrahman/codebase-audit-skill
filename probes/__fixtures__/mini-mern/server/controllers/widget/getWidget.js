const { getWidgetById } = require('../../database/queries/widget');
module.exports = async (req, res) => res.json(await getWidgetById(req.params.id));
