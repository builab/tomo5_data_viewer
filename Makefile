# make test SESSION=/path/to/Session [TILT=/path/to/TiltSeries]   click through the viewer, report errors
# make check                                                       syntax checks only (Python; JavaScript if node is installed)
.PHONY: test check

test:
	@test -n "$(SESSION)" || (echo "Usage: make test SESSION=/path/to/Session [TILT=/path/to/TiltSeries]"; exit 2)
	python3 tests/smoke_test.py --session "$(SESSION)" $(if $(TILT),--tilt "$(TILT)")

check:
	python3 -m py_compile tomo5_viewer.py tests/smoke_test.py
	@if command -v node >/dev/null; then python3 -c "s=open('index.html').read(); a=s.index('<script>')+8; open('/tmp/tomo5_page.js','w').write(s[a:s.index('</script>')])" && node --check /tmp/tomo5_page.js && echo "index.html: JavaScript OK"; fi
