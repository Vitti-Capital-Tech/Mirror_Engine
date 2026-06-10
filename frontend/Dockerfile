FROM node:20-alpine

WORKDIR /app

# Copy package dependencies
COPY package*.json ./

# Install dependencies
RUN npm install --legacy-peer-deps

# Copy application files
COPY . .

# Expose Next.js port
EXPOSE 3000

# Start Next.js development server
CMD ["npm", "run", "dev"]
