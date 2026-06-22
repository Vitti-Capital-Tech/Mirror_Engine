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
};

module.exports = nextConfig;
