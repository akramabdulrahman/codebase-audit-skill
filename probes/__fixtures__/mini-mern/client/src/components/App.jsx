import { createBrowserRouter } from 'react-router';
import * as R from '../constants/navigationRoutes';
import PrivateRoute from './PrivateRoute';
import WidgetEdit from './WidgetEdit';
import MyAccount from './MyAccount';
export const router = createBrowserRouter([
  { path: R.WIDGET_EDIT, element: <PrivateRoute Component={WidgetEdit} allowedRoles={['admin','orgAdmin']} /> },
  { path: R.ME, element: <PrivateRoute Component={MyAccount} allowedRoles={['admin','orgAdmin','member']} /> },
]);
