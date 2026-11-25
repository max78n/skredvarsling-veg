import sys
m=open('mal_varsler.html').read()
m=m.replace('/*LEAFLET_CSS*/',open('leaflet.css').read()).replace('<script src="klient.js"></script>','<script>\n'+open('klient.js').read()+'</script>')
open(sys.argv[1],'w').write(m)
