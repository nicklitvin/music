# OMR on AWS Lambda

Reading a sheet (oemer) is the only part of the backend that needs real
CPU: a couple of minutes of two cores per page, a few times a day. On the
server, that meant paying around the clock for an instance big enough for
those few minutes. On Lambda, each page runs as its own invocation:

- **Every page runs at once**, so a 10-page sheet takes about as long as
  one page, instead of ten times as long.
- **It is paid per second of use.** At 3.5 GB (2 vCPUs), a page costs
  about 1 cent. Lambda's free tier is 400,000 GB-seconds a month, about
  600 pages.
- **The server only has to serve the site and the live tracking**, which
  a t3.small (2 GB, ~$15 a month) handles. A c7a.large is ~$75.

The function runs the same code the server would
(`app/services/omr_remote.py` → `oemer_engine.extract_page`), with the same
pinned libraries. On a test page it gave identical notes. Pages go in the
request and results come back in the response; nothing is stored in S3 or
anywhere else.

## Setting it up

1. **AWS, once.** Open AWS CloudShell (the `>_` icon in the top bar of
   the AWS console), in the region the server is in (Ohio, `us-east-2`).
   Upload `setup-aws.sh` (Actions → Upload file), then run it with the
   server's instance ID (EC2 → Instances):

   ```bash
   bash setup-aws.sh i-0123456789abcdef0
   ```

   This creates the image repository, the function's role, a role that only
   this repo's GitHub Actions (main branch) can use to deploy it, and the
   server's permission to call the function. The comment at the top of
   the script lists exactly what it creates. Nothing else is touched, and
   it is safe to run again.

2. **GitHub, once.** Under Settings → Secrets and variables → Actions →
   **Variables**, add the two values the script printed: `AWS_DEPLOY_ROLE_ARN`
   and `AWS_REGION`.

3. **Deploy the function.** Under Actions → "OMR Lambda" → Run workflow.
   It builds the image (~5 minutes), creates the function and checks it
   starts. After that it redeploys by itself whenever OMR code changes.

4. **Point the server at it.** Add the following to the backend's
   environment, then restart it:

   ```
   OMR_LAMBDA_FUNCTION=music-omr
   OMR_LAMBDA_REGION=us-east-2
   ```

   Leaving `OMR_LAMBDA_FUNCTION` empty brings back on-server OMR.

5. **Shrink the server** to a t3.small, once a sheet has gone through.

## Seeing what it did

The server logs one line per page:
`page 3: 112s on Lambda (2 vCPUs), 640 notes`. The function's own log
is in CloudWatch, under the log group `/aws/lambda/music-omr`. It has
the same per-stage timings the server used to log.

## Tuning

`OMR_LAMBDA_MEMORY_MB` (a GitHub variable, default 3538) sets the
function's memory, and the CPU comes with it: 1 vCPU per 1,769 MB, up to
6 at 10,240. oemer's nets make poor use of more than 2 threads (see "How
long OMR takes" in the top-level README). More memory makes each page a
little faster, but costs proportionally more per page.

New AWS accounts are also capped at 3008 MB per function (about 1.7
vCPUs) until AWS raises it, so this repo sets `OMR_LAMBDA_MEMORY_MB=3008`.
A page peaks at about 2.3 GB, so it fits.

New AWS accounts can run only 10 Lambdas at once. The setup script prints
this account's limit. Pages beyond it wait their turn, and
`OMR_LAMBDA_CONCURRENCY` (default 10) caps how many one sheet sends at
once.
