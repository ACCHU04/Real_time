# Flink 1.20 with Python (PyFlink) and the Kafka + JDBC connectors.
FROM flink:1.20.1-java17

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip wget \
 && ln -sf /usr/bin/python3 /usr/bin/python \
 && pip3 install --no-cache-dir apache-flink==1.20.1 "setuptools<75" \
 && rm -rf /var/lib/apt/lists/*

# Connector jars go into Flink's lib folder, so every job can use them.
ARG MAVEN=https://repo.maven.apache.org/maven2
RUN wget -q -P /opt/flink/lib \
      $MAVEN/org/apache/flink/flink-sql-connector-kafka/3.3.0-1.20/flink-sql-connector-kafka-3.3.0-1.20.jar \
      $MAVEN/org/apache/flink/flink-connector-jdbc/3.3.0-1.20/flink-connector-jdbc-3.3.0-1.20.jar \
      $MAVEN/org/postgresql/postgresql/42.7.4/postgresql-42.7.4.jar \
 && chown flink:flink /opt/flink/lib/*.jar
USER flink
