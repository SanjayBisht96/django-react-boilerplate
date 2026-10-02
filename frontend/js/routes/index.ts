import { createBrowserRouter } from 'react-router';

import { usersLoader } from '@/js/loaders';
import Home from '@/js/pages/Home';
import Payments from '@/js/pages/Payments';
import Users from '@/js/pages/Users';

const router = createBrowserRouter([
  { index: true, Component: Home },
  { path: 'users', Component: Users, loader: usersLoader },
  { path: 'payments', Component: Payments },
]);

export default router;
