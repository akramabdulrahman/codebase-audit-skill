import { connect } from 'react-redux';
import { updateMe } from '../actions/userActions';
const MyAccount = () => null;
export default connect(null, { updateMe })(MyAccount);
