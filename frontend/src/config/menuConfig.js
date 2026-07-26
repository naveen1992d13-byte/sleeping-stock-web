import { User, Upload, Package, FileSpreadsheet, ClipboardCheck, ClipboardList, BarChart3, Globe, Megaphone, HelpCircle, Smartphone } from 'lucide-react';

export const APPLICATION_MENU_ITEMS = [
  { path: '/users', label: 'User Hub', icon: User, id: 'users', adminOnly: true },
  { path: '/upload', label: 'Upload Center', icon: Upload, id: 'upload', adminOnly: true },
  { path: '/products', label: 'Product Hub', icon: Package, id: 'products' },
  { path: '/product-history', label: 'Product Hub History', icon: FileSpreadsheet, id: 'product-history', adminOnly: true },
  { path: '/orders', label: 'Order Desk', icon: ClipboardCheck, id: 'orders' },
  { path: '/requests', label: 'Request Center', icon: ClipboardList, id: 'requests' },
  { path: '/reports', label: 'Reports', icon: BarChart3, id: 'reports', adminOnly: true },
  { path: '/analytics', label: 'Analytics', icon: Globe, id: 'analytics', adminOnly: true },
  { path: '/', label: 'Notice Board', icon: Megaphone, id: 'dashboard' },
  { path: '/query', label: 'Query Desk', icon: HelpCircle, id: 'query' },
  { path: '/sleeping-stock-mobile', label: 'Sleeping Stock Mobile', icon: Smartphone, id: 'sleeping-stock-mobile', allRoles: true },
];

export const APPLICATION_PERMISSION_LABELS = APPLICATION_MENU_ITEMS.map(item => item.label);
