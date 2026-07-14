import { WIDGET_UPDATED } from '../constants/actionTypes';
export default (state = {}, action) => { switch (action.type) { case WIDGET_UPDATED: return { ...state, w: action.payload }; default: return state; } };
