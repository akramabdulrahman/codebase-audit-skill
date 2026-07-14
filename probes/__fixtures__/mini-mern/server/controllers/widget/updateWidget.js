const boom = require('boom');
const { getWidgetById, updateWidgetById } = require('../../database/queries/widget');
module.exports = async (req, res, next) => {
  const widget = await getWidgetById(req.params.id);
  if (widget.org.toString() !== req.user.org.toString()) return next(boom.forbidden('nope'));  // ownership guard
  return res.json(await updateWidgetById(req.params.id, req.body));
};
