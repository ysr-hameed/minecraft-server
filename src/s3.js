const fs = require('fs');
const path = require('path');
const config = require('./config');

function enabled() {
  return Boolean(config.aws.bucket && config.aws.accessKeyId && config.aws.secretAccessKey);
}

async function upload(filePath) {
  if (!enabled()) return { ok: false, skipped: true, message: 'S3 is not configured' };
  let S3Client;
  let PutObjectCommand;
  try {
    ({ S3Client, PutObjectCommand } = require('@aws-sdk/client-s3'));
  } catch {
    return { ok: false, skipped: true, message: 'Install @aws-sdk/client-s3 to enable cloud backups' };
  }

  const clientOptions = {
    region: config.aws.region,
    credentials: {
      accessKeyId: config.aws.accessKeyId,
      secretAccessKey: config.aws.secretAccessKey,
    },
  };
  if (config.aws.endpoint) {
    clientOptions.endpoint = config.aws.endpoint;
    clientOptions.forcePathStyle = true;
  }

  const key = `${config.aws.prefix.replace(/\/+$/, '')}/${path.basename(filePath)}`;
  try {
    const client = new S3Client(clientOptions);
    await client.send(
      new PutObjectCommand({
        Bucket: config.aws.bucket,
        Key: key,
        Body: fs.createReadStream(filePath),
        ContentType: 'application/zip',
      })
    );
    return { ok: true, message: `Uploaded to s3://${config.aws.bucket}/${key}` };
  } catch (error) {
    return { ok: false, message: `S3 upload failed: ${error.message}` };
  }
}

module.exports = { enabled, upload };
