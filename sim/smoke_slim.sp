* SKY130 smoke test: one nfet_01v8, DC op + Id-Vgs sweep
.lib models/sky130_slim.lib.spice tt
XM1 d g 0 0 sky130_fd_pr__nfet_01v8 W=2 L=0.5 nf=1
Vd d 0 0.9
Vg g 0 0.7
.control
op
let id = -i(Vd)
print id
dc Vg 0 1.8 0.1
let idc = -i(Vd)
print idc[18]
quit
.endc
.end
