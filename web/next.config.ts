import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 生产部署用 standalone（自包含 Node server，无需 node_modules）
  output: "standalone",
  // 开发期将 /api/* 代理到后端，避免 CORS
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
