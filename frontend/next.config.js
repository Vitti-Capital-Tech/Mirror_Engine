/** @type {import('next').NextConfig} */
const nextConfig = {
  typescript: {
    // Skip strict type checking during build to save memory/time on t3.micro
    ignoreBuildErrors: true,
  },
  eslint: {
    // Skip lint checks during build to speed up compilation
    ignoreDuringBuilds: true,
  },
  // /architecture serves a static page from public/ rather than an app route.
  // It is read by people who do not have a dashboard login (and should not need
  // one), and it carries its own styles, so routing it through the app shell
  // would only add an auth redirect and a CSS collision for no benefit.
  async rewrites() {
    return [{ source: '/architecture', destination: '/architecture.html' }];
  },
};

module.exports = nextConfig;
