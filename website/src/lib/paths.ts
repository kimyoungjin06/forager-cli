const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');

export function sitePath(path: string): string {
  if (path === '/') {
    return `${basePath}/`;
  }

  return `${basePath}${path.startsWith('/') ? path : `/${path}`}`;
}
