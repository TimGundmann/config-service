FROM openjdk:10.0.2-13-jre-sid

COPY target/config-server.jar /app/config-server.jar

ENV ENCRYPT_KEY = $ENCRYPT_KEY

CMD ["java", "-jar", "/app/config-server.jar"]
