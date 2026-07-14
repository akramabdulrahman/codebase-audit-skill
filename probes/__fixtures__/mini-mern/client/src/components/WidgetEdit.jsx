import { connect } from 'react-redux';
import { updateWidget, deleteWidget } from '../actions/widgetActions';
const WidgetEdit = () => null;
export default connect(null, { updateWidget, deleteWidget })(WidgetEdit);
