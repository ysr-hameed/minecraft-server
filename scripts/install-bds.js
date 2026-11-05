#!/usr/bin/env node
const manager = require('../src/manager');

(async () => {
  try {
    const releases = await manager.latestRelease(true);
    if (!releases.latest) throw new Error('The official download API did not return a Linux server link');
    console.log(`Latest official Bedrock: ${releases.latest.version}`);
    console.log(`URL: ${releases.latest.url}`);
    const result = await manager.install(releases.latest, { force: true });
    console.log(result.message);
    process.exit(0);
  } catch (error) {
    console.error(`Install failed: ${error.message}`);
    process.exit(1);
  }
})();
