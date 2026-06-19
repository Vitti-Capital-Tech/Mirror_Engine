FROM node:20-alpine

WORKDIR /app

# Copy package dependencies
COPY package*.json ./

# Install dependencies
RUN npm install --legacy-peer-deps

# Copy application files
COPY . .

# Build the Next.js application for production
RUN npm run build

# Expose Next.js port
EXPOSE 3000

# Start Next.js production server
CMD ["npm", "run", "start"]
