node {
   def mvnHome
   stage('Preparation') { 
      git 'https://github.com/TimGundmann/config-server.git'
      env.JAVA_HOME="${tool 'jdk 9'}"
      env.PATH="/var/lib/jenkins/.local/bin:${env.PATH}"
      mvnHome = tool 'maven 3.3.9'
   }
   stage("version update") {
       pom = readMavenPom file: 'pom.xml'
       newVersion = pom.version + "." + env.BUILD_NUMBER
       sh "'${mvnHome}/bin/mvn' versions:set -DnewVersion=${newVersion}"
       currentBuild.displayName = "Version: ${newVersion}"
   }
   stage('Build') {
     sh "'${mvnHome}/bin/mvn' clean package -U"
   }
   stage('Docker Deploy') {
        sh "docker-compose stop"
        sh "docker-compose build"
        sh "docker-compose up -d"
   }
   

}