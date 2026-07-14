import { ME_UPDATED } from '../constants/actionTypes';
export default (state = {}, action) => { switch (action.type) { case ME_UPDATED: return { ...state, me: action.payload }; default: return state; } };
