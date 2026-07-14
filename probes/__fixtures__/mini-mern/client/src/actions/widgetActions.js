import axios from 'axios';
import { WIDGET_UPDATED } from '../constants/actionTypes';
export const updateWidget = (id, data) => async (dispatch) => {
  const res = await axios.patch(`/api/widgets/${id}`, data);
  dispatch({ type: WIDGET_UPDATED, payload: res.data });
};
export const deleteWidget = (id) => async (dispatch) => {
  await axios.delete(`/api/widgets/${id}`);
  dispatch({ type: WIDGET_UPDATED, payload: id });
};
