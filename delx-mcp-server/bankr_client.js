#!/usr/bin/env node
/**
 * 🦊💜 Bankr SDK Client Wrapper for Delx MCP
 * ==========================================
 * Calls Bankr SDK with x402 payment support.
 * 
 * Usage:
 *   node bankr_client.js "What is my ETH balance on Base?"
 */

// Note: In a real implementation, you'd use the official Bankr SDK
// For now, we use the HTTP API directly

const https = require('https');

// 🔧 Configuration
const BANKR_API_URL = process.env.BANKR_API_URL || 'https://api.bankr.bot';
const BANKR_API_KEY = process.env.BANKR_API_KEY || '';

/**
 * 💬 Submit a prompt to Bankr
 */
async function submitPrompt(prompt) {
    return new Promise((resolve, reject) => {
        const data = JSON.stringify({ prompt });

        const url = new URL(BANKR_API_URL);

        const options = {
            hostname: url.hostname,
            port: url.port || 443,
            path: '/agent/prompt',
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': BANKR_API_KEY,
                'Content-Length': Buffer.byteLength(data)
            }
        };

        const req = https.request(options, (res) => {
            let body = '';

            res.on('data', (chunk) => {
                body += chunk;
            });

            res.on('end', () => {
                try {
                    const result = JSON.parse(body);
                    resolve(result);
                } catch (e) {
                    resolve({ raw: body });
                }
            });
        });

        req.on('error', (e) => {
            reject(e);
        });

        req.write(data);
        req.end();
    });
}

/**
 * 🔄 Poll for job result
 */
async function pollJobResult(jobId, maxAttempts = 30) {
    for (let i = 0; i < maxAttempts; i++) {
        await new Promise(r => setTimeout(r, 2000)); // Wait 2s between polls

        const result = await new Promise((resolve, reject) => {
            const url = new URL(BANKR_API_URL);

            const options = {
                hostname: url.hostname,
                port: url.port || 443,
                path: `/agent/job/${jobId}`,
                method: 'GET',
                headers: {
                    'X-API-Key': BANKR_API_KEY
                }
            };

            const req = https.request(options, (res) => {
                let body = '';
                res.on('data', (chunk) => body += chunk);
                res.on('end', () => {
                    try {
                        resolve(JSON.parse(body));
                    } catch (e) {
                        resolve({ raw: body });
                    }
                });
            });

            req.on('error', reject);
            req.end();
        });

        if (result.status === 'completed') {
            return result;
        }

        if (result.status === 'failed') {
            throw new Error(result.error || 'Job failed');
        }

        console.error(`⏳ Polling... (${i + 1}/${maxAttempts})`);
    }

    throw new Error('Timeout waiting for job completion');
}

/**
 * 🚀 Main entry point
 */
async function main() {
    const prompt = process.argv[2];

    if (!prompt) {
        console.error('Usage: node bankr_client.js "Your prompt here"');
        process.exit(1);
    }

    if (!BANKR_API_KEY) {
        console.error('❌ BANKR_API_KEY environment variable not set');
        process.exit(1);
    }

    console.log('🦊 Submitting to Bankr...');
    console.log(`📝 Prompt: ${prompt}`);

    try {
        // Submit prompt
        const submitResult = await submitPrompt(prompt);

        if (!submitResult.jobId) {
            // Direct response
            console.log('✅ Result:', JSON.stringify(submitResult, null, 2));
            return;
        }

        console.log(`📋 Job ID: ${submitResult.jobId}`);

        // Poll for result
        const finalResult = await pollJobResult(submitResult.jobId);

        console.log('\n✅ Result:');
        console.log(JSON.stringify(finalResult, null, 2));

    } catch (error) {
        console.error('❌ Error:', error.message);
        process.exit(1);
    }
}

main();
