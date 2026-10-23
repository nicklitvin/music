# The OMR Lambda (app/services/omr_remote.py). Build from backend/:
#   docker build --platform linux/amd64 -f lambda/Dockerfile -t music-omr .
# x86_64 on purpose: it is what the pinned onnxruntime has been verified on.
FROM public.ecr.aws/lambda/python:3.12

# matplotlib (imported by oemer, never used for drawing here) wants a
# writable config dir, and /tmp is the only writable place on Lambda.
ENV MPLCONFIGDIR=/tmp/matplotlib

COPY lambda/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && pip install --no-cache-dir --no-deps oemer==0.1.8

COPY app ${LAMBDA_TASK_ROOT}/app

# Download oemer's weights and build their any-size copies now: the image
# is read-only once deployed. The TensorFlow copies of the weights
# (weights.h5, 110 MB) are never read on the onnxruntime path. The any-size
# copies are written via a temp file, which is owner-only; Lambda runs as an
# unprivileged user, hence the chmod.
RUN cd ${LAMBDA_TASK_ROOT} \
 && python -c "from app.services import oemer_engine; oemer_engine.prepare_models()" \
 && checkpoints="$(python -c 'import oemer; print(oemer.MODULE_PATH)')/checkpoints" \
 && rm -f "$checkpoints"/*/weights.h5 \
 && chmod -R a+rX "$checkpoints"

CMD ["app.services.omr_remote.handler"]
