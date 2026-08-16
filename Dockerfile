# Everything a render needs, so the install story is one command.
#
# The host dependencies here are the reason this image exists: ffmpeg, a
# browser, and — for the xvfb backend — a virtual display. That last one is
# the strongest argument for a container. The xvfb backend needs to control
# its own X server, and an image controls that where a host merely hopes.
#
# Playwright publish an image with the browsers and their system libraries
# already installed and version-matched. Building on it avoids the most
# tedious failure in this whole stack: a Chromium that will not start
# because some libnss or libasound is missing from a slim base.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

# ffmpeg and ffprobe from apt rather than the bundled extra. In an image we
# control the platform, so a real system build is available and preferred —
# it is better optimised, and it brings ffprobe, which the bundled package
# does not ship.
#
# xvfb and xdotool are only used by the xvfb backend. They are small, and
# leaving them out would make the image useless for the one case a container
# helps most.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        xvfb \
        xdotool \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# fonts-dejavu-core is not optional. ffmpeg drawtext is handed a font path,
# and a missing one renders title cards as blank frames with a zero exit
# code — a successful render of a broken video, which is the failure mode
# this project keeps having to defend against.

WORKDIR /app

# Dependencies first, so editing source does not invalidate the layer that
# takes the longest to build.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Demos are mounted, not baked. The config is the user's, and it is Python,
# so baking it in would mean rebuilding the image to change a line of
# narration.
VOLUME ["/demo"]
WORKDIR /demo

# Default to the diagnostic. Running the image with no arguments should tell
# you whether the environment is sound, not fail at an unexplained ffmpeg
# error twenty seconds into something else.
ENTRYPOINT ["python", "-m"]
CMD ["demotape.doctor"]
