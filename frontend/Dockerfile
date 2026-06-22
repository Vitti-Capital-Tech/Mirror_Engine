FROM node:20-alpine

WORKDIR /app

# Add build arguments for Next.js static compilation
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_WS_URL

# Set them as system environment variables
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL

# Copy package dependencies
COPY package*.json ./

# Install dependencies
RUN npm install --legacy-peer-deps

# Copy application files
COPY . .

# Remove any local env files to force Next.js to use Docker build args
RUN rm -f .env .env.local .env.production .env.development

# Build the Next.js application for production
RUN npm run build

# Expose Next.js port
EXPOSE 3000

# Start Next.js production server
CMD ["npm", "run", "start"]
