import { createRouter, createWebHistory } from 'vue-router'
import { toast } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'
import { APP_BASE } from '@/utils/base'

// Sync Log, Reports and Store Connection are manager pages. The Sales Rep list is open to
// reps too: the server limits it to themselves and their team.
const MANAGERS = ['System Manager', 'Sales Manager']

// The customer login: My Orders and its detail page are the only pages it may open.
// Not a `roles` check: holding the Field Sales Retailer role is not the same as being
// the retailer login (Administrator holds every role); the guard checks session.isRetailer.
const RETAILER_ROUTES = ['MyOrders', 'MyOrder']

// meta.title: page title and last breadcrumb; meta.parent: breadcrumb parent route name;
// meta.public: no session needed (the login page itself).
const routes = [
  { path: '/', redirect: { name: 'Dashboard' } },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/Login.vue'),
    meta: { public: true, title: 'Log in' },
  },
  {
    path: '/my-orders',
    name: 'MyOrders',
    component: () => import('@/pages/MyOrders.vue'),
    meta: { title: 'My Orders', retailerOnly: true },
  },
  {
    path: '/my-orders/:name',
    name: 'MyOrder',
    component: () => import('@/pages/MyOrderDetail.vue'),
    props: true,
    meta: { title: 'Order', parent: 'MyOrders', retailerOnly: true },
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/pages/Dashboard.vue'),
    meta: { title: 'Dashboard' },
  },
  {
    path: '/retailers',
    name: 'Retailers',
    component: () => import('@/pages/Retailers.vue'),
    meta: { title: 'Customers' },
  },
  {
    path: '/retailers/:name',
    name: 'Retailer',
    component: () => import('@/pages/RetailerDetail.vue'),
    props: true,
    meta: { parent: 'Retailers' },
  },
  {
    path: '/visits',
    name: 'Visits',
    component: () => import('@/pages/Visits.vue'),
    meta: { title: 'Visits' },
  },
  {
    path: '/visits/:name',
    name: 'Visit',
    component: () => import('@/pages/VisitDetail.vue'),
    props: true,
    meta: { parent: 'Visits' },
  },
  {
    path: '/orders',
    name: 'Orders',
    component: () => import('@/pages/Orders.vue'),
    meta: { title: 'Field Orders' },
  },
  {
    path: '/orders/:name',
    name: 'Order',
    component: () => import('@/pages/OrderDetail.vue'),
    props: true,
    meta: { parent: 'Orders' },
  },
  {
    path: '/products',
    name: 'Products',
    component: () => import('@/pages/Products.vue'),
    meta: { title: 'Products' },
  },
  {
    path: '/products/:name',
    name: 'Product',
    component: () => import('@/pages/ProductDetail.vue'),
    props: true,
    meta: { parent: 'Products' },
  },
  {
    path: '/sales-reps',
    name: 'SalesReps',
    component: () => import('@/pages/SalesReps.vue'),
    meta: { title: 'Sales Reps' },
  },
  {
    // A rep can open their own page (and their team's): the server limits which reps load.
    path: '/sales-reps/:name',
    name: 'SalesRep',
    component: () => import('@/pages/SalesRepDetail.vue'),
    props: true,
    meta: { parent: 'SalesReps' },
  },
  {
    path: '/commission',
    name: 'Commission',
    component: () => import('@/pages/Commission.vue'),
    meta: { title: 'Commission', feature: 'commission' },
  },
  {
    path: '/commission/statements/:name',
    name: 'CommissionStatement',
    component: () => import('@/pages/CommissionStatement.vue'),
    props: true,
    meta: { parent: 'Commission', feature: 'commission' },
  },
  {
    path: '/sync-log',
    name: 'SyncLogs',
    component: () => import('@/pages/SyncLogs.vue'),
    meta: { title: 'Sync Log', roles: MANAGERS },
  },
  {
    path: '/sync-log/:name',
    name: 'SyncLogEntry',
    component: () => import('@/pages/SyncLogDetail.vue'),
    props: true,
    meta: { parent: 'SyncLogs', roles: MANAGERS },
  },
  {
    path: '/sync-failures',
    name: 'SyncFailures',
    component: () => import('@/pages/SyncFailures.vue'),
    meta: { title: 'Sync Failures', roles: MANAGERS },
  },
  {
    path: '/notifications',
    name: 'Notifications',
    component: () => import('@/pages/Notifications.vue'),
    meta: { title: 'Notifications' },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/pages/Settings.vue'),
    meta: { title: 'Settings', roles: MANAGERS },
  },
  {
    path: '/reports',
    name: 'Reports',
    component: () => import('@/pages/Reports.vue'),
    meta: { title: 'Reports', roles: MANAGERS },
  },
  {
    path: '/reports/rep-performance',
    name: 'RepPerformance',
    component: () => import('@/pages/RepPerformance.vue'),
    meta: { title: 'Rep Performance', parent: 'Reports', roles: MANAGERS },
  },
  {
    path: '/reports/rep-commission',
    name: 'RepCommission',
    component: () => import('@/pages/RepCommission.vue'),
    meta: { title: 'Rep Commission', parent: 'Reports', roles: MANAGERS, feature: 'commission' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/pages/NotFound.vue'),
    meta: { title: 'Not Found' },
  },
]

// Served at /fieldsales and its alias /fs (hooks.website_route_rules): keep whichever
// was opened (utils/base.js, re-exported for existing importers).
export { APP_BASE }

const router = createRouter({
  history: createWebHistory(APP_BASE),
  routes,
})

router.beforeEach(async (to) => {
  // The login page loads without a session (guests reach it through the page gate).
  if (to.meta.public) return true

  const session = useSessionStore()
  if (!(await session.load())) return false

  // A customer login stays on My Orders; staff pages redirect it back.
  if (session.isRetailer && !RETAILER_ROUTES.includes(to.name)) return { name: 'MyOrders' }
  // And My Orders is the customer login's alone: a staff user (even one that also holds
  // the Field Sales Retailer role, e.g. Administrator) has no retailer of its own.
  if (to.meta.retailerOnly && !session.isRetailer) {
    toast.error('You do not have access to ' + (to.meta.title || 'this page'))
    return { name: 'Dashboard' }
  }

  if (to.meta.roles && !session.hasRole(...to.meta.roles)) {
    toast.error('You do not have access to ' + (to.meta.title || 'this page'))
    return { name: 'Dashboard' }
  }
  if (!session.canSee(to.meta)) {
    toast.info('Commission is turned off for this company')
    return { name: 'Dashboard' }
  }
})

router.afterEach((to) => {
  const title = to.meta.title || to.params.name
  document.title = title ? `${title} · Field Sales` : 'Field Sales'
})

export default router
