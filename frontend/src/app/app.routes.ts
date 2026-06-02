import { Routes } from '@angular/router';
import { authGuard } from './core/guards/auth.guard';
import { setupGuard } from './core/guards/setup.guard';

export const routes: Routes = [
  { path: '', redirectTo: '/dashboard', pathMatch: 'full' },
  {
    path: 'setup',
    loadComponent: () =>
      import('./features/setup/setup.component').then(m => m.SetupComponent),
  },
  {
    path: 'auth',
    children: [
      {
        path: 'login',
        loadComponent: () =>
          import('./features/auth/login/login.component').then(m => m.LoginComponent),
      },
      {
        path: 'register',
        loadComponent: () =>
          import('./features/auth/register/register.component').then(m => m.RegisterComponent),
      },
    ],
  },
  {
    path: 'dashboard',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent),
  },
  {
    path: 'create',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/creation/creation.component').then(m => m.CreationComponent),
  },
  {
    path: 'chat/:agentId',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/chat/chat.component').then(m => m.ChatComponent),
  },
  {
    path: 'tools/:agentId',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/tools/tools.component').then(m => m.ToolsComponent),
  },
  {
    path: 'projects/:agentId',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/projects/projects.component').then(m => m.ProjectsComponent),
  },
  {
    path: 'tasks/:agentId',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/projects/projects.component').then(m => m.ProjectsComponent),
  },
  {
    path: 'memory/:agentId',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/memory/memory.component').then(m => m.MemoryComponent),
  },
  {
    path: 'brain/:agentId',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/brain/brain.component').then(m => m.BrainComponent),
  },
  {
    path: 'settings',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/settings/settings.component').then(m => m.SettingsComponent),
  },
  {
    path: 'settings/api-keys',
    canActivate: [setupGuard, authGuard],
    loadComponent: () =>
      import('./features/api-keys/api-keys.component').then(m => m.ApiKeysComponent),
  },
  { path: '**', redirectTo: '/dashboard' },
];
