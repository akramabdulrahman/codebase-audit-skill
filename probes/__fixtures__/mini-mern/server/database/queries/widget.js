const Widget = require('../models/Widget');
module.exports.getWidgetById = (id) => Widget.findById(id);
module.exports.updateWidgetById = (id, data) => Widget.findByIdAndUpdate(id, data);
module.exports.deleteWidgetById = (id) => Widget.findByIdAndDelete(id);
