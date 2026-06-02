/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'https://agentic-bug-hunter.onrender.com/:path*',
      },
    ]
  },
}
module.exports = nextConfig