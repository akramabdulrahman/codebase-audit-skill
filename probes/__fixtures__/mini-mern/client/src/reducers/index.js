import { combineReducers } from 'redux';
import widget from './widgetReducer';
import user from './userReducer';
export default combineReducers({ widget, user });
