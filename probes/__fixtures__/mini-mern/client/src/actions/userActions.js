import axios from 'axios';
import { ME_UPDATED } from '../constants/actionTypes';
export const updateMe = (data) => async (dispatch) => {
  const res = await axios.patch('/api/me', data);
  dispatch({ type: ME_UPDATED, payload: res.data });
};
