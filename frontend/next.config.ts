import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',

  async rewrites() {
    const apiBase = process.env.INTERNAL_API_URL ?? 'http://127.0.0.1:5055';
    return [
      {
        source: '/api/:path*',
        destination: `${apiBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
