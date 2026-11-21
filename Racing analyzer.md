# Racing analyzer

## Background
I am a crew member on a 40 foot regatta sailboat. We are racing regattas in Northern Europe, mostly Norway, Sweden, Denmark, Germany etc. Most or all regattas is arranged and scored using ORC (orc.org).We are competing against some of the best teams doing bigboat racing in the said region. We have instruments onboard which shows the usual stuff regarding wind, boatspeed, GPS data, boat attitude etc. 

We plan to install a OpenPlotter (https://openmarine.net/openplotter) on a Raspberry Pi 4 to be permantently installed in the boat. This will be connected to the NMEA2000 network and subscribe to all data on the bus. I plan to have all this stored in a InfluxDB v1 with quite high resolution (e.g. 1 second sampling interval).

## The boat
- Name: Sons of Hurricanes
- Class: Sinergia 40
- Sail number: NOR 15666
- Polar data and measurement certificate: available on orc.org

## Objective
- Based on the InfluxDB data, we want to analyze our performance. At the moment, we are unsure what to gain from it, but should be similar or exceed what we can get from existing services online.

## Inspiration
- Njord - https://www.sailnjord.com/ 
- Chartedsail - https://www.chartedsails.com/

## Softwarea implementation
- Shall be installed and operated on the RPI4
- Use Python and all relevant publicly available libraries