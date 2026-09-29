const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${process.env.API_INTERNAL_URL || "http://localhost:8000"}/api/:path*` },
      { source: "/health", destination: `${process.env.API_INTERNAL_URL || "http://localhost:8000"}/health` }
    ];
  }
};
export default nextConfig;
