import { Routes } from '@angular/router';
import { authGuard, adminGuard, setupGuard, setupRedirectGuard } from './core/guards';

export const routes: Routes = [
  {
    path: 'setup',
    canActivate: [setupGuard],
    loadComponent: () =>
      import('./pages/setup/setup.component').then((m) => m.SetupComponent),
  },
  {
    path: 'login',
    canActivate: [setupRedirectGuard],
    loadComponent: () =>
      import('./pages/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: '',
    canActivate: [authGuard, adminGuard],
    loadComponent: () =>
      import('./pages/layout/admin-layout.component').then((m) => m.AdminLayoutComponent),
    children: [
      {
        path: '',
        loadComponent: () =>
          import('./pages/dashboard/dashboard.component').then((m) => m.DashboardComponent),
      },
      {
        path: 'users',
        loadComponent: () =>
          import('./pages/users/users.component').then((m) => m.UsersComponent),
      },
      {
        path: 'users/:id',
        loadComponent: () =>
          import('./pages/users/user-detail.component').then((m) => m.UserDetailComponent),
      },
      {
        path: 'agents',
        loadComponent: () =>
          import('./pages/agents/agents.component').then((m) => m.AgentsComponent),
      },
      {
        path: 'usage',
        loadComponent: () =>
          import('./pages/usage/usage.component').then((m) => m.UsageComponent),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
