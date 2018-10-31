#!/bin/bash

set -e

REPO=$1

# These must be outside the heredoc below otherwise the script won't error.
COMMIT="$(cd $REPO && git log --format="%H" -n 1)"
BRANCH="$(cd $REPO && git symbolic-ref --short HEAD)"
DESCRIBE="$(cd $REPO && git describe --dirty)"

TMPFILE_H=$(tempfile -s .h 2>/dev/null || mktemp --suffix=.h)
TMPFILE_C=$(tempfile -s .c 2>/dev/null || mktemp --suffix=.c)

UPLATFORM="$(echo $PLATFORM | tr '[:lower:]' '[:upper:]')"
UTARGET="$(echo $TARGET | tr '[:lower:]' '[:upper:]')"

cat > $TMPFILE_H <<EOF
#ifndef __VERSION_DATA_H
#define __VERSION_DATA_H

extern const char* board;
extern const char* target;

extern const char* git_commit;
extern const char* git_branch;
extern const char* git_describe;
extern const char* git_status;

#endif  // __VERSION_DATA_H
EOF

cat > $TMPFILE_C <<EOF

#ifndef PLATFORM_$UPLATFORM
#error "Version mismatch - PLATFORM_$UPLATFORM not defined!"
#endif
const char* board = "$PLATFORM";

#ifndef TARGET_$UTARGET
#error "Version mismatch - TARGET_$UTARGET not defined!"
#endif
const char* target = "$TARGET";

const char* git_commit = "$COMMIT";
const char* git_branch = "$BRANCH";
const char* git_describe = "$DESCRIBE";
const char* git_status =
    "    --\r\n"
$(cd $REPO && git status --short | sed -e's-^-   "    -' -e's-$-\\r\\n"-')
    "    --\r\n";

EOF

if ! cmp -s $TMPFILE_H version_data.h; then
	echo "Updating version_data.h"
	mv $TMPFILE_H version_data.h
fi

if ! cmp -s $TMPFILE_C version_data.c; then
	echo "Updating version_data.c"
	mv $TMPFILE_C version_data.c
fi
